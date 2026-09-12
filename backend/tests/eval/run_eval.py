"""
tests/eval/run_eval.py

Master Evaluation Runner
========================

This is the script that ties everything together.
Run this script to get the complete accuracy and quality report.

Usage:
    # Standard evaluation (uses existing Supabase data):
    python tests/eval/run_eval.py

    # Re-run evaluations and capture RAG data:
    python tests/eval/run_eval.py --fresh

    # CI/CD mode (exits with code 1 if thresholds not met):
    python tests/eval/run_eval.py --ci

The script does four things:
    1. Loads the golden dataset (manually labeled ground truth)
    2. Queries Supabase for evaluation results on those vendors
    3. Computes accuracy: system recommendation vs manual label
    4. Runs RAGAS on captured RAG data (if available)
    5. Outputs a summary report

Exit codes (for CI/CD):
    0 = All gates passed
    1 = One or more gates failed (blocks deployment)
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / "backend" / ".env")

from ragas_eval import compute_accuracy_against_golden_dataset


async def fetch_evaluations_from_supabase() -> list[dict]:
    """
    Fetch evaluation results from Supabase for vendors in the golden dataset.
    Returns the most recent evaluation for each vendor.
    """
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import select, desc
    from app.db.models import Evaluation
    from app.db.database import DATABASE_URL

    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    evaluations = []
    async with async_session() as session:
        # Get most recent evaluation for each vendor
        result = await session.execute(
            select(Evaluation).order_by(desc(Evaluation.created_at))
        )
        rows = result.scalars().all()

        seen_vendors = set()
        for row in rows:
            vendor_key = row.vendor_name.lower().strip()
            if vendor_key not in seen_vendors:
                seen_vendors.add(vendor_key)
                evaluations.append({
                    "vendor_name":      row.vendor_name,
                    "recommendation":   row.recommendation,
                    "composite_score":  row.composite_score,
                    "confidence":       row.confidence,
                    "evaluation_id":    row.evaluation_id,
                    "created_at":       row.created_at.isoformat(),
                })

    await engine.dispose()
    return evaluations


def generate_report(accuracy_result: dict, ragas_result: dict | None) -> dict:
    """
    Generate a combined report suitable for:
    - Console output
    - Saving to JSON (for CI/CD artifact)
    - Resume metrics
    """
    report = {
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "accuracy":      accuracy_result,
        "ragas":         ragas_result,
        "overall_pass":  accuracy_result.get("passes_gate", False),
        "summary":       {},
    }

    # Build summary for quick reading
    acc = accuracy_result.get("accuracy_pct", 0)
    total = accuracy_result.get("total_cases", 0)

    summary_lines = [
        f"Accuracy:          {acc:.1f}% ({accuracy_result.get('correct', 0)}/{total} correct)",
    ]

    if ragas_result and "scores" in ragas_result:
        scores = ragas_result["scores"]
        summary_lines.append(f"Faithfulness:      {scores.get('faithfulness', 0):.3f}")
        summary_lines.append(f"Answer Relevancy:  {scores.get('answer_relevancy', 0):.3f}")
        summary_lines.append(f"Context Precision: {scores.get('context_precision', 0):.3f}")
        report["overall_pass"] = (
            report["overall_pass"]
            and ragas_result.get("passes_gate", False)
        )

    gate_status = "✅ ALL GATES PASSED" if report["overall_pass"] else "❌ GATE FAILED — DEPLOYMENT BLOCKED"
    summary_lines.append(f"\nCI/CD Gate:        {gate_status}")

    report["summary"] = {
        "lines": summary_lines,
        "resume_metric": accuracy_result.get("resume_metric", ""),
    }

    return report


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="VendorRisk Evaluation Runner")
    parser.add_argument("--fresh", action="store_true",
                        help="Re-run evaluations for golden dataset vendors")
    parser.add_argument("--ci",   action="store_true",
                        help="CI mode: exit with code 1 if gates fail")
    parser.add_argument("--no-ragas", action="store_true",
                        help="Skip RAGAS evaluation (faster, accuracy only)")
    args = parser.parse_args()

    print("🚀 VendorRisk Evaluation Suite")
    print("=" * 60)

    golden_path = Path(__file__).parent / "golden_dataset.json"
    if not golden_path.exists():
        print(f"❌ Golden dataset not found at {golden_path}")
        sys.exit(1)

    # Step 1: Fetch existing evaluation results from Supabase
    print("\n📡 Fetching evaluations from Supabase...")
    try:
        evaluations = await fetch_evaluations_from_supabase()
        print(f"   Found {len(evaluations)} vendor evaluations in database")
    except Exception as e:
        print(f"   ⚠ Could not connect to Supabase: {e}")
        print("   Continuing with accuracy check only (0 matches expected)")
        evaluations = []

    # Step 2: Accuracy evaluation
    accuracy_result = compute_accuracy_against_golden_dataset(
        golden_dataset_path=str(golden_path),
        supabase_evaluations=evaluations,
    )

    # Step 3: RAGAS evaluation (optional)
    ragas_result = None
    if not args.no_ragas:
        captured_path = Path(__file__).parent / "captured_rag_data.json"
        if captured_path.exists():
            print("\n🔬 Found captured RAG data — running RAGAS evaluation...")
            from ragas_eval import run_ragas_evaluation
            with open(captured_path) as f:
                captured = json.load(f)
            ragas_result = run_ragas_evaluation(captured)
        else:
            print("\n⚠ No captured RAG data found.")
            print("  Run evaluations with RAG capture enabled to get RAGAS metrics.")
            print("  The accuracy metric above is sufficient for the resume claim.")

    # Step 4: Generate report
    report = generate_report(accuracy_result, ragas_result)

    # Step 5: Save report
    report_path = Path(__file__).parent / "eval_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    # Step 6: Print summary
    print("\n" + "=" * 60)
    print("📊 EVALUATION SUMMARY")
    print("=" * 60)
    for line in report["summary"]["lines"]:
        print(f"   {line}")
    print(f"\n📄 Report saved to {report_path}")

    # Resume metric
    if report["summary"].get("resume_metric"):
        print(f"\n🎓 RESUME METRIC:")
        print(f'   "{report["summary"]["resume_metric"]}"')

    # CI/CD exit
    if args.ci and not report["overall_pass"]:
        print("\n❌ Evaluation gate FAILED. Blocking deployment.")
        sys.exit(1)

    print("\n✅ Evaluation complete.")


if __name__ == "__main__":
    asyncio.run(main())
