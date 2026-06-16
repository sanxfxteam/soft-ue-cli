// Copyright soft-ue-expert. All Rights Reserved.

#include "Tools/Editor/RunAutomationTool.h"
#include "SoftUEBridgeEditorModule.h"
#include "Engine/Engine.h"
#include "Modules/ModuleManager.h"
#include "IAutomationControllerModule.h"
#include "IAutomationControllerManager.h"
#include "IAutomationReport.h"
#include "Misc/AutomationEvent.h"
#include "Misc/App.h"
#include "Misc/AutomationTest.h"
#include "HAL/PlatformTime.h"
#include "Containers/Ticker.h"

/**
 * Shared state for the async automation run, tracked by a FTSTicker delegate.
 * The ticker fires each engine frame, checks progress, and calls OnComplete when done.
 */
struct FAutomationRunState
{
	enum class EPhase : uint8
	{
		Discovery,
		Running,
		Complete
	};

	// Raw pointer — the controller is owned by the AutomationController module and
	// lives for the entire editor session. Outlives our ticker state.
	IAutomationControllerManager* Controller = nullptr;
	TArray<FString> InputTests;
	float TimeoutSeconds = 60.0f;
	double StartTime = 0.0;
	double DiscoveryStartTime = 0.0;
	EPhase Phase = EPhase::Discovery;

	FOnBridgeToolComplete OnComplete;
	FTSTicker::FDelegateHandle TickHandle;
	FDelegateHandle RefreshedHandle;
	FDelegateHandle TestsCompleteHandle;

	bool bDiscoveryDone = false;
	bool bTestsDone = false;
	bool bHasBootstrapped = false;

	~FAutomationRunState()
	{
		// Ensure delegates are cleaned up
		if (Controller)
		{
			if (RefreshedHandle.IsValid())
			{
				Controller->OnTestsRefreshed().Remove(RefreshedHandle);
			}
			if (TestsCompleteHandle.IsValid())
			{
				Controller->OnTestsComplete().Remove(TestsCompleteHandle);
			}
		}
	}
};

FString URunAutomationTool::GetToolDescription() const
{
	return TEXT("Run a list of automation tests via the Session Frontend and return the results and logs. "
				"Accepts exact test names or wildcard patterns (e.g. 'ProjectShiva.Abilities.*').");
}

TMap<FString, FBridgeSchemaProperty> URunAutomationTool::GetInputSchema() const
{
	TMap<FString, FBridgeSchemaProperty> Schema;

	FBridgeSchemaProperty TestsProp;
	TestsProp.Type = TEXT("array");
	TestsProp.Description = TEXT("List of automation tests to run (exact names or wildcards).");
	TestsProp.bRequired = true;
	Schema.Add(TEXT("tests"), TestsProp);

	FBridgeSchemaProperty TimeoutProp;
	TimeoutProp.Type = TEXT("number");
	TimeoutProp.Description = TEXT("Total execution timeout in seconds.");
	TimeoutProp.bRequired = false;
	Schema.Add(TEXT("timeout"), TimeoutProp);

	FBridgeSchemaProperty TimeoutPerTestProp;
	TimeoutPerTestProp.Type = TEXT("number");
	TimeoutPerTestProp.Description = TEXT("Timeout per test in seconds (used if total timeout is not specified).");
	TimeoutPerTestProp.bRequired = false;
	Schema.Add(TEXT("timeout_per_test"), TimeoutPerTestProp);

	return Schema;
}

void URunAutomationTool::ExecuteAsync(
	const TSharedPtr<FJsonObject>& Arguments,
	const FBridgeToolContext& /*Context*/,
	const FOnBridgeToolComplete& OnComplete)
{
	// 1. Parse Arguments
	TArray<FString> InputTests;
	const TArray<TSharedPtr<FJsonValue>>* TestsJsonArray = nullptr;
	if (Arguments->TryGetArrayField(TEXT("tests"), TestsJsonArray) && TestsJsonArray)
	{
		for (const TSharedPtr<FJsonValue>& Val : *TestsJsonArray)
		{
			InputTests.Add(Val->AsString());
		}
	}

	if (InputTests.Num() == 0)
	{
		OnComplete.ExecuteIfBound(FBridgeToolResult::Error(TEXT("Required parameter 'tests' is empty or invalid.")));
		return;
	}

	float TimeoutSeconds = -1.0f;
	float TimeoutPerTest = -1.0f;
	if (Arguments->HasField(TEXT("timeout")))
	{
		TimeoutSeconds = GetFloatArgOrDefault(Arguments, TEXT("timeout"), 60.0f);
	}
	else
	{
		TimeoutPerTest = GetFloatArgOrDefault(Arguments, TEXT("timeout_per_test"), 60.0f);
	}

	UE_LOG(LogSoftUEBridgeEditor, Log, TEXT("run-automation: Starting async test execution (TimeoutSeconds: %.1fs, TimeoutPerTest: %.1fs)"), TimeoutSeconds, TimeoutPerTest);

	// 2. Load Automation Controller Module
	IAutomationControllerModule& AutomationControllerModule = FModuleManager::LoadModuleChecked<IAutomationControllerModule>(TEXT("AutomationController"));
	IAutomationControllerManagerRef ControllerRef = AutomationControllerModule.GetAutomationController();
	IAutomationControllerManager* Controller = &ControllerRef.Get();

	// Ensure the local worker is loaded so it responds to discovery requests
	FModuleManager::LoadModuleChecked<IModuleInterface>(TEXT("AutomationWorker"));

	Controller->SetRequestedTestFlags(EAutomationTestFlags_ApplicationContextMask | EAutomationTestFlags_FilterMask);

	// 3. Set up shared state
	TSharedRef<FAutomationRunState> State = MakeShared<FAutomationRunState>();
	State->Controller = Controller;
	State->InputTests = MoveTemp(InputTests);
	State->TimeoutSeconds = TimeoutSeconds > 0.0f ? TimeoutSeconds : 60.0f;
	State->StartTime = FPlatformTime::Seconds();
	State->DiscoveryStartTime = FPlatformTime::Seconds();
	State->OnComplete = OnComplete;

	// 4. Check if tests are already discovered
	TSharedRef<AutomationFilterCollection> EmptyFilters = MakeShared<AutomationFilterCollection>();
	Controller->SetFilter(EmptyFilters);
	Controller->SetVisibleTestsEnabled(true);

	TArray<FString> AllTestNames;
	Controller->GetFilteredTestNames(AllTestNames);

	// Filter out empty/whitespace names
	AllTestNames.RemoveAll([](const FString& Name) { return Name.TrimStartAndEnd().IsEmpty(); });

	if (AllTestNames.Num() > 0)
	{
		// Tests already discovered — skip straight to running
		State->bDiscoveryDone = true;
		State->Phase = FAutomationRunState::EPhase::Discovery; // Will transition to Running on first tick
	}
	else
	{
		// Need to discover — request workers
		State->RefreshedHandle = Controller->OnTestsRefreshed().AddLambda([State]()
		{
			State->bDiscoveryDone = true;
		});

		Controller->RequestAvailableWorkers(FApp::GetSessionId());
		UE_LOG(LogSoftUEBridgeEditor, Log, TEXT("run-automation: Discovering workers and tests..."));
	}

	// 5. Register ticker — this fires every engine frame, driving the state machine
	State->TickHandle = FTSTicker::GetCoreTicker().AddTicker(
		FTickerDelegate::CreateLambda([State, TimeoutPerTest](float DeltaTime) -> bool
		{
			const double ElapsedTotal = FPlatformTime::Seconds() - State->StartTime;
			IAutomationControllerManager* Controller = State->Controller;

			switch (State->Phase)
			{
			case FAutomationRunState::EPhase::Discovery:
			{
				// Tick the controller to process MessageBus responses
				Controller->Tick();

				if (State->bDiscoveryDone)
				{
					// Clean up discovery delegate
					if (State->RefreshedHandle.IsValid())
					{
						Controller->OnTestsRefreshed().Remove(State->RefreshedHandle);
						State->RefreshedHandle.Reset();
					}

					// Get discovered tests
					TArray<FString> AllTestNames;
					Controller->GetFilteredTestNames(AllTestNames);
					AllTestNames.RemoveAll([](const FString& Name) { return Name.TrimStartAndEnd().IsEmpty(); });

					if (AllTestNames.Num() == 0)
					{
						if (!State->bHasBootstrapped && State->InputTests.Num() > 0)
						{
							State->bHasBootstrapped = true;
							UE_LOG(LogSoftUEBridgeEditor, Warning, TEXT("run-automation: No tests discovered. Bootstrapping Automation Controller via '%s'..."), *State->InputTests[0]);
							
							if (GEngine)
							{
								GEngine->Exec(nullptr, *FString::Printf(TEXT("Automation RunTests %s"), *State->InputTests[0]));
							}

							// Restart discovery phase
							State->bDiscoveryDone = false;
							State->DiscoveryStartTime = FPlatformTime::Seconds();
							State->RefreshedHandle = Controller->OnTestsRefreshed().AddLambda([State]()
							{
								State->bDiscoveryDone = true;
							});
							Controller->RequestAvailableWorkers(FApp::GetSessionId());
							return true; // Keep ticking and retry discovery
						}

						UE_LOG(LogSoftUEBridgeEditor, Error, TEXT("run-automation: No tests discovered after refresh."));
						State->OnComplete.ExecuteIfBound(FBridgeToolResult::Error(
							TEXT("Failed to discover any automation tests. Ensure the session frontend is initialized.")));
						return false; // Remove ticker
					}

					// Filter tests by input patterns
					TArray<FString> FilteredTestNames;
					for (const FString& TestName : AllTestNames)
					{
						for (const FString& Pattern : State->InputTests)
						{
							if (UBridgeToolBase::MatchesWildcard(TestName, Pattern))
							{
								FilteredTestNames.AddUnique(TestName);
								break;
							}
						}
					}

					if (FilteredTestNames.Num() == 0)
					{
						FString FoundNames = FString::Printf(TEXT("No available automation tests matched the provided names/patterns. Discovered %d total tests. First 20:"), AllTestNames.Num());
						for (int32 i = 0; i < FMath::Min(20, AllTestNames.Num()); ++i)
						{
							FoundNames += TEXT("\n  ") + AllTestNames[i];
						}
						State->OnComplete.ExecuteIfBound(FBridgeToolResult::Error(FoundNames));
						return false; // Remove ticker
					}

					UE_LOG(LogSoftUEBridgeEditor, Log, TEXT("run-automation: Enabling %d matched tests:"), FilteredTestNames.Num());
					for (const FString& EnabledTest : FilteredTestNames)
					{
						UE_LOG(LogSoftUEBridgeEditor, Log, TEXT("  - %s"), *EnabledTest);
					}

					if (TimeoutPerTest > 0.0f)
					{
						State->TimeoutSeconds = FilteredTestNames.Num() * TimeoutPerTest;
						UE_LOG(LogSoftUEBridgeEditor, Log, TEXT("run-automation: Calculated dynamic timeout: %.1fs (%.1fs per test for %d tests)"), State->TimeoutSeconds, TimeoutPerTest, FilteredTestNames.Num());
					}

					// Start tests
					State->TestsCompleteHandle = Controller->OnTestsComplete().AddLambda([State]()
					{
						State->bTestsDone = true;
					});

					Controller->StopTests();
					Controller->SetEnabledTests(FilteredTestNames);
					Controller->RunTests();

					State->Phase = FAutomationRunState::EPhase::Running;
					UE_LOG(LogSoftUEBridgeEditor, Log, TEXT("run-automation: Executing tests..."));
				}
				else if (FPlatformTime::Seconds() - State->DiscoveryStartTime > 30.0)
				{
					// Discovery timeout
					UE_LOG(LogSoftUEBridgeEditor, Error, TEXT("run-automation: Discovery timed out after 30s."));
					State->OnComplete.ExecuteIfBound(FBridgeToolResult::Error(
						TEXT("Test discovery timed out after 30 seconds.")));
					return false; // Remove ticker
				}
				break;
			}

			case FAutomationRunState::EPhase::Running:
			{
				Controller->Tick();

				if (State->bTestsDone)
				{
					UE_LOG(LogSoftUEBridgeEditor, Log, TEXT("run-automation: Tests completed in %.1fs."), ElapsedTotal);
					State->OnComplete.ExecuteIfBound(
						URunAutomationTool::BuildResultFromReports(*Controller, false));
					return false; // Remove ticker
				}
				else if (ElapsedTotal >= State->TimeoutSeconds)
				{
					UE_LOG(LogSoftUEBridgeEditor, Warning, TEXT("run-automation: Tests timed out after %.1fs!"), ElapsedTotal);
					State->OnComplete.ExecuteIfBound(
						URunAutomationTool::BuildResultFromReports(*Controller, true));
					return false; // Remove ticker
				}
				break;
			}

			default:
				return false; // Remove ticker
			}

			return true; // Continue ticking
		}),
		0.0f // Tick every frame
	);
}

FBridgeToolResult URunAutomationTool::BuildResultFromReports(
	IAutomationControllerManager& Controller,
	bool bDidTimeout)
{
	TArray<TSharedPtr<IAutomationReport>> EnabledReports = Controller.GetEnabledReports();
	TArray<TSharedPtr<FJsonValue>> ReportsJsonArray;

	int32 TotalTests = EnabledReports.Num();
	int32 PassedCount = 0;
	int32 FailedCount = 0;

	for (const TSharedPtr<IAutomationReport>& Report : EnabledReports)
	{
		if (!Report.IsValid())
		{
			continue;
		}

		TSharedPtr<FJsonObject> ReportObj = MakeShareable(new FJsonObject);
		ReportObj->SetStringField(TEXT("test_name"), Report->GetCommand());
		ReportObj->SetStringField(TEXT("display_name"), Report->GetDisplayName());
		ReportObj->SetStringField(TEXT("full_path"), Report->GetFullTestPath());

		int32 ClusterIndex = 0;
		int32 PassIndex = Report->GetCurrentPassIndex(ClusterIndex);
		if (PassIndex < 0)
		{
			PassIndex = 0;
		}

		const FAutomationTestResults& Results = Report->GetResults(ClusterIndex, PassIndex);

		FString StateStr = TEXT("NotRun");
		switch (Results.State)
		{
			case EAutomationState::NotRun:
				StateStr = TEXT("NotRun");
				break;
			case EAutomationState::InProcess:
				StateStr = TEXT("InProcess");
				break;
			case EAutomationState::Success:
				StateStr = TEXT("Success");
				PassedCount++;
				break;
			case EAutomationState::Fail:
				StateStr = TEXT("Failure");
				FailedCount++;
				break;
			case EAutomationState::Skipped:
				StateStr = TEXT("Skipped");
				break;
		}

		ReportObj->SetStringField(TEXT("state"), StateStr);
		ReportObj->SetNumberField(TEXT("duration_seconds"), Results.Duration);
		ReportObj->SetNumberField(TEXT("errors_count"), Results.GetErrorTotal());
		ReportObj->SetNumberField(TEXT("warnings_count"), Results.GetWarningTotal());

		// Entries (Logs, Warnings, Errors)
		TArray<TSharedPtr<FJsonValue>> EntriesJsonArray;
		for (const FAutomationExecutionEntry& Entry : Results.GetEntries())
		{
			TSharedPtr<FJsonObject> EntryObj = MakeShareable(new FJsonObject);

			FString TypeStr = TEXT("Info");
			switch (Entry.Event.Type)
			{
				case EAutomationEventType::Info:
					TypeStr = TEXT("Info");
					break;
				case EAutomationEventType::Warning:
					TypeStr = TEXT("Warning");
					break;
				case EAutomationEventType::Error:
					TypeStr = TEXT("Error");
					break;
			}

			EntryObj->SetStringField(TEXT("type"), TypeStr);
			EntryObj->SetStringField(TEXT("message"), Entry.Event.Message);
			EntryObj->SetStringField(TEXT("context"), Entry.Event.Context);
			EntryObj->SetStringField(TEXT("filename"), Entry.Filename);
			EntryObj->SetNumberField(TEXT("line_number"), Entry.LineNumber);
			EntryObj->SetStringField(TEXT("timestamp"), Entry.Timestamp.ToIso8601());

			EntriesJsonArray.Add(MakeShareable(new FJsonValueObject(EntryObj)));
		}
		ReportObj->SetArrayField(TEXT("entries"), EntriesJsonArray);

		ReportsJsonArray.Add(MakeShareable(new FJsonValueObject(ReportObj)));
	}

	TSharedPtr<FJsonObject> Result = MakeShareable(new FJsonObject);
	Result->SetBoolField(TEXT("success"), !bDidTimeout && (FailedCount == 0));
	Result->SetBoolField(TEXT("timed_out"), bDidTimeout);
	Result->SetNumberField(TEXT("total_tests"), TotalTests);
	Result->SetNumberField(TEXT("passed_tests"), PassedCount);
	Result->SetNumberField(TEXT("failed_tests"), FailedCount);
	Result->SetArrayField(TEXT("reports"), ReportsJsonArray);

	return FBridgeToolResult::Json(Result);
}
