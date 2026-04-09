from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sdd_demo import ROOT, resolve_scenario_root, run_trial

MODES = ["tests_only", "baseline", "full_sdd_no_operator", "full_sdd"]
DEFAULT_TRIALS_PER_TASK = 3
RESULT_FIELDS = [
    "task_id",
    "mode",
    "accepted",
    "retries",
    "rejections",
    "policy_violations",
    "errors",
    "scenario",
    "trial",
]

try:
    from tqdm import tqdm  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    tqdm = None


@dataclass(frozen=True)
class RunSpec:
    scenario_name: str
    scenario_root: Path
    mode: str
    task_path: Path
    task_id: str
    trial: int


class ProgressReporter:
    """Human-friendly run progress with optional tqdm support."""

    def __init__(self, style: str, *, total: int, detailed: bool, trials_per_task: int, start_index: int = 0):
        self.style = style
        self.total = total
        self.detailed = detailed
        self.index = start_index
        self.trials_per_task = trials_per_task
        self._pbar = None
        if self.style == "tqdm":
            self._pbar = tqdm(total=total, initial=start_index, desc="Tiny SDD eval", dynamic_ncols=True)

    def _line(self, text: str) -> None:
        if self.style == "off":
            return
        if self._pbar is not None:
            self._pbar.write(text)
        else:
            print(text)

    def start_scenario(self, scenario_name: str, task_count: int) -> None:
        self._line(
            f"[SCENARIO] {scenario_name} | tasks={task_count} | trials_per_task={self.trials_per_task} | modes={','.join(MODES)}"
        )

    def start_mode(self, scenario_name: str, mode: str) -> None:
        self._line(f"[MODE] {scenario_name} -> {mode}")

    def run_done(self, spec: RunSpec, row: dict) -> None:
        self.index += 1
        status = "OK" if row["accepted"] else "FAIL"
        if self._pbar is not None:
            self._pbar.set_description(
                f"{spec.scenario_name} | {spec.mode} | {spec.task_id} | trial {spec.trial + 1}/{self.trials_per_task}"
            )
            self._pbar.set_postfix_str(
                f"{status} rty={row['retries']} rej={row['rejections']} pol={row['policy_violations']}"
            )
            self._pbar.update(1)
            if self.detailed:
                self._line(
                    f"[{self.index}/{self.total}] {status} "
                    f"{spec.scenario_name}/{spec.mode}/{spec.task_id}/trial{spec.trial + 1} "
                    f"retries={row['retries']} rejections={row['rejections']} policy_violations={row['policy_violations']}"
                )
        elif self.style == "plain":
            self._line(
                f"[{self.index}/{self.total}] {status} "
                f"{spec.scenario_name}/{spec.mode}/{spec.task_id}/trial{spec.trial + 1} "
                f"retries={row['retries']} rejections={row['rejections']} policy_violations={row['policy_violations']}"
            )

    def close(self) -> None:
        if self._pbar is not None:
            self._pbar.close()


def _discover_scenarios() -> list[tuple[str, Path]]:
    """Return (scenario_name, scenario_root) pairs."""
    dataset_root = ROOT / "dataset_sdd"
    if not dataset_root.exists():
        return [("root", ROOT)]

    scenarios: list[tuple[str, Path]] = []
    for path in sorted(dataset_root.glob("scenario*")):
        if not path.is_dir():
            continue
        if (path / "tasks").exists() and (path / "architecture_card.yaml").exists() and (path / "app").exists():
            scenarios.append((path.name, path))

    return scenarios or [("root", ROOT)]


def _resolve_progress_style(requested: str) -> str:
    if requested == "off":
        return "off"
    if requested == "plain":
        return "plain"
    if requested == "tqdm":
        if tqdm is None:
            return "plain"
        return "tqdm"
    # auto
    if tqdm is not None and sys.stdout.isatty():
        return "tqdm"
    return "plain"


def _list_tasks(scenario_root: Path, max_tasks: int | None = None) -> list[Path]:
    tasks = sorted((scenario_root / "tasks").glob("task*.yaml"))
    if max_tasks is not None:
        tasks = tasks[:max_tasks]
    return tasks


def _collect_runs(scenarios: list[tuple[str, Path]], trials_per_task: int, max_tasks: int | None = None) -> list[RunSpec]:
    runs: list[RunSpec] = []
    for scenario_name, scenario_root in scenarios:
        tasks = _list_tasks(scenario_root, max_tasks=max_tasks)
        if not tasks:
            raise FileNotFoundError(f"No tasks found in: {scenario_root / 'tasks'}")
        for mode in MODES:
            for task_path in tasks:
                task_id = task_path.stem
                for trial in range(trials_per_task):
                    runs.append(
                        RunSpec(
                            scenario_name=scenario_name,
                            scenario_root=scenario_root,
                            mode=mode,
                            task_path=task_path,
                            task_id=task_id,
                            trial=trial,
                        )
                    )
    return runs


def _run_key(scenario: str, mode: str, task_id: str, trial: int) -> tuple[str, str, str, int]:
    return scenario, mode, task_id, trial


def _normalize_loaded_row(row: dict[str, str], *, default_scenario: str) -> dict[str, Any]:
    out: dict[str, Any] = dict(row)
    out["scenario"] = out.get("scenario") or default_scenario
    out["trial"] = int(out.get("trial") or 0)
    out["accepted"] = str(out.get("accepted")).lower() == "true"
    out["retries"] = int(out.get("retries") or 0)
    out["rejections"] = int(out.get("rejections") or 0)
    out["policy_violations"] = int(out.get("policy_violations") or 0)
    return out


def _load_existing_rows(path: Path, *, default_scenario: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row:
                continue
            rows.append(_normalize_loaded_row(row, default_scenario=default_scenario))
    return rows


def _print_mode_summary(rows: list[dict], prefix: str) -> None:
    print(prefix)
    for mode in MODES:
        subset = [r for r in rows if r["mode"] == mode]
        if not subset:
            print(f"{mode:10s} | no data")
            continue
        success = sum(r["accepted"] for r in subset) / len(subset)
        compliant_success = sum((r["accepted"] and int(r["policy_violations"]) == 0) for r in subset) / len(subset)
        pol = sum(r["policy_violations"] for r in subset) / len(subset)
        rej = sum(r["rejections"] for r in subset) / len(subset)
        retr = sum(r["retries"] for r in subset) / len(subset)
        unresolved_rej = sum((int(r["rejections"]) > 0 and not r["accepted"]) for r in subset) / len(subset)
        agent_error_rate = sum(("AGENT_ERROR:" in str(r.get("errors", ""))) for r in subset) / len(subset)
        print(
            f"{mode:10s} | success={success:.2f} | compliant_success={compliant_success:.2f} "
            f"| avg_policy_violations={pol:.2f} | avg_rejections={rej:.2f} | avg_retries={retr:.2f} "
            f"| unresolved_rejections={unresolved_rej:.2f} | agent_error_rate={agent_error_rate:.2f}"
        )


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RESULT_FIELDS)
        writer.writeheader()
        writer.writerows([{k: row.get(k) for k in RESULT_FIELDS} for row in rows])


def _append_csv_row(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(exist_ok=True)
    write_header = (not path.exists()) or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RESULT_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow({k: row.get(k) for k in RESULT_FIELDS})


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Tiny SDD evaluator across tasks and modes.")
    parser.add_argument("--scenario", dest="scenario", default=None, help="Scenario name under dataset_sdd/ (e.g., scenario1)")
    parser.add_argument(
        "--max-scenarios",
        dest="max_scenarios",
        type=int,
        default=None,
        help="When --scenario is not set, run only the first N discovered scenarios (sorted).",
    )
    parser.add_argument("--trials", dest="trials", type=int, default=DEFAULT_TRIALS_PER_TASK, help="Trials per task.")
    parser.add_argument(
        "--max-tasks",
        dest="max_tasks",
        type=int,
        default=None,
        help="Run only the first N tasks per selected scenario (sorted by task file name).",
    )
    parser.add_argument(
        "--max-retries",
        dest="max_retries",
        type=int,
        default=2,
        help="Maximum repair retries per run_trial attempt.",
    )
    parser.add_argument(
        "--adaptive-retries",
        action="store_true",
        help="Enable adaptive retry budgeting (bonus retries are applied only to full_sdd).",
    )
    parser.add_argument(
        "--adaptive-extra-retries",
        dest="adaptive_extra_retries",
        type=int,
        default=2,
        help="Extra retries added by adaptive mode when full_sdd failures look recoverable.",
    )
    parser.add_argument("--resume", action="store_true", help="Resume from existing per-scenario summary CSV files.")
    parser.add_argument(
        "--progress",
        choices=["auto", "tqdm", "plain", "off"],
        default="auto",
        help="Progress display style (default: auto).",
    )
    parser.add_argument(
        "--detailed",
        action="store_true",
        help="Print one line for every completed run (useful with tqdm).",
    )
    args = parser.parse_args()
    if args.trials <= 0:
        raise ValueError("--trials must be >= 1")
    if args.max_retries < 0:
        raise ValueError("--max-retries must be >= 0")
    if args.adaptive_extra_retries < 0:
        raise ValueError("--adaptive-extra-retries must be >= 0")
    if args.max_scenarios is not None and args.max_scenarios <= 0:
        raise ValueError("--max-scenarios must be >= 1")
    if args.max_tasks is not None and args.max_tasks <= 0:
        raise ValueError("--max-tasks must be >= 1")

    if args.scenario:
        selected_root = resolve_scenario_root(args.scenario)
        scenarios = [(Path(selected_root).name if selected_root != ROOT else "root", selected_root)]
        if args.max_scenarios is not None:
            print("Ignoring --max-scenarios because --scenario was provided.")
    else:
        scenarios = _discover_scenarios()
        if args.max_scenarios is not None:
            scenarios = scenarios[: args.max_scenarios]
            if not scenarios:
                raise FileNotFoundError("No scenarios available after applying --max-scenarios filter.")

    runs = _collect_runs(scenarios, args.trials, max_tasks=args.max_tasks)
    progress_style = _resolve_progress_style(args.progress)

    all_rows: list[dict] = []
    scenario_rows: dict[str, list[dict]] = {}
    scenario_task_counts = {
        name: len(_list_tasks(root, max_tasks=args.max_tasks)) for name, root in scenarios
    }
    completed_keys: set[tuple[str, str, str, int]] = set()
    scenario_output_paths = {name: root / "results" / "summary.csv" for name, root in scenarios}

    # Prepare existing/new result buffers.
    for scenario_name, _scenario_root in scenarios:
        out_path = scenario_output_paths[scenario_name]
        if args.resume:
            existing = _load_existing_rows(out_path, default_scenario=scenario_name)
            if existing:
                scenario_rows[scenario_name] = list(existing)
                all_rows.extend(existing)
                for row in existing:
                    completed_keys.add(_run_key(scenario_name, row["mode"], row["task_id"], int(row["trial"])))
        else:
            if out_path.exists():
                out_path.unlink()

    pending_runs = [spec for spec in runs if _run_key(spec.scenario_name, spec.mode, spec.task_id, spec.trial) not in completed_keys]
    reporter = ProgressReporter(
        progress_style,
        total=len(runs),
        detailed=args.detailed,
        trials_per_task=args.trials,
        start_index=len(completed_keys),
    )

    try:
        active_scenario = None
        active_mode = None
        for spec in pending_runs:
            if active_scenario != spec.scenario_name:
                active_scenario = spec.scenario_name
                reporter.start_scenario(spec.scenario_name, scenario_task_counts[spec.scenario_name])
                active_mode = None
            if active_mode != spec.mode:
                active_mode = spec.mode
                reporter.start_mode(spec.scenario_name, spec.mode)

            row = run_trial(
                spec.task_path,
                spec.mode,
                spec.trial,
                max_retries=args.max_retries,
                scenario_root=spec.scenario_root,
                adaptive_retries=args.adaptive_retries,
                adaptive_extra_retries=args.adaptive_extra_retries,
            )
            row["scenario"] = spec.scenario_name
            row["trial"] = spec.trial
            scenario_rows.setdefault(spec.scenario_name, []).append(row)
            all_rows.append(row)
            _append_csv_row(scenario_output_paths[spec.scenario_name], row)
            reporter.run_done(spec, row)
    finally:
        reporter.close()

    for scenario_name, scenario_root in scenarios:
        rows = scenario_rows.get(scenario_name, [])
        if not rows:
            continue
        scenario_out = scenario_root / "results" / "summary.csv"
        _write_csv(scenario_out, rows)

    # Global summary first.
    _print_mode_summary(all_rows, "=== Tiny SDD LLM summary (all selected scenarios) ===")

    # Then each scenario summary.
    print("=== Per-scenario summaries ===")
    for scenario_name in sorted(scenario_rows):
        _print_mode_summary(scenario_rows[scenario_name], f"[{scenario_name}]")

    # Combined CSV output when multiple scenarios are selected.
    if len(scenarios) > 1:
        combined_out = ROOT / "dataset_sdd" / "results" / "summary_all_scenarios.csv"
        _write_csv(combined_out, all_rows)
        print(f"Saved combined results to: {combined_out}")
    else:
        only_scenario = scenarios[0][1]
        print(f"Saved detailed results to: {only_scenario / 'results' / 'summary.csv'}")


if __name__ == "__main__":
    main()
