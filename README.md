# CARET: Contract-Aware Repository Editing and Tooling for
Governed Agentic Code Generation

Compact benchmark for evaluating Spec-Driven Development (SDD) behavior in coding agents.

The same tasks are executed in 4 modes:
- `tests_only`
- `baseline`
- `full_sdd_no_operator`
- `full_sdd`

## Repository Layout

Core files:
- `evaluator.py`: batch runner across scenarios, tasks, modes, and trials
- `sdd_demo.py`: single-trial runner and governance checks
- `llm_agent.py`: mock and real-LLM patch proposal backends

Dataset:
- `dataset_sdd/scenario*/`
- each scenario contains:
  - `app/`
  - `tasks/task*.yaml`
  - `architecture_card.yaml`

Scenario size:
- hard scenarios: `scenario1`, `scenario10`, `scenario11` (15 tasks each)
- standard scenarios: `scenario2..scenario9`, `scenario12` (12 tasks each)

## Mode Semantics

- `tests_only`: one-shot run, tests gate only, no retry loop
- `baseline`: tests gate with generic retry guidance
- `full_sdd_no_operator`: static + trace + tests verification, no compliance-operator patch assist
- `full_sdd`: static + trace + tests verification with compliance-operator patch assist

## Quick Start

Use Python 3.10+.

```bash
pip install -r requirements.txt
```

Run all discovered scenarios:

```bash
python evaluator.py
```

Run one scenario:

```bash
python evaluator.py --scenario scenario1
```

Limit workload:

```bash
python evaluator.py --max-scenarios 3
python evaluator.py --scenario scenario1 --max-tasks 5
python evaluator.py --max-scenarios 3 --max-tasks 8
```

Retry settings:

```bash
python evaluator.py --max-retries 4
python evaluator.py --adaptive-retries --adaptive-extra-retries 2
```

Resume an interrupted scenario run:

```bash
python evaluator.py --scenario scenario1 --resume
```

Progress styles:

```bash
python evaluator.py --progress tqdm
python evaluator.py --progress plain --detailed
python evaluator.py --progress off
```

Single task/trial:

```bash
python sdd_demo.py --scenario scenario1
python sdd_demo.py dataset_sdd/scenario1/tasks/task01_include_inactive.yaml full_sdd
```

## Real LLM Mode

Recommended: create a local `.env` from `.env.example` and keep secrets out of git.

PowerShell example:

```powershell
$env:LLM_MODE="real"
$env:LLM_MODEL="gpt-5.4-nano"
$env:OPENAI_API_KEY="..."
$env:OPENAI_TIMEOUT_SECONDS="90"
$env:OPENAI_MAX_RETRIES="3"
$env:OPENAI_RETRY_BACKOFF_SECONDS="2.0"
python evaluator.py --scenario scenario1
```

If `LLM_MODE` is not `real` (or no key is set), the mock backend is used.

## Outputs

Per-scenario CSV:
- `dataset_sdd/<scenario>/results/summary.csv`

Combined CSV (multi-scenario runs):
- `dataset_sdd/results/summary_all_scenarios.csv`

CSV columns:
- `task_id`, `mode`, `accepted`, `retries`, `rejections`, `policy_violations`, `errors`, `scenario`, `trial`

## Metrics

Console summary reports:
- `success`: accepted runs / total runs
- `compliant_success`: accepted runs with `policy_violations = 0` / total runs
- `avg_policy_violations`: average governance issues per run
- `avg_rejections`: average verifier rejections before final outcome
- `avg_retries`: average extra attempts
- `unresolved_rejections`: share of runs rejected at least once and still not accepted
- `agent_error_rate`: share of runs ending with `AGENT_ERROR`

Interpretation:
- use `success` for pure functional delivery
- use `compliant_success` for functional + governance-safe delivery
- if `success` is high and `compliant_success` is low, tests pass but policy constraints still fail (for example PII leakage or missing preconditions)

## Submission Checklist

- Do not commit `.env` (contains API keys)
- Use `.env.example` for shared configuration template
- Decide whether generated `dataset_sdd/**/results/*.csv` should be versioned or ignored for your submission
