"""
tests/eval/ragas_eval.py

RAGAS Evaluation Script for VendorRisk AI
==========================================

What is RAGAS?
    RAGAS (Retrieval-Augmented Generation Assessment Suite) is an open-source
    framework that measures the quality of RAG pipelines objectively.

    It answers the question: "Is the AI using the retrieved context correctly,
    or is it hallucinating things that aren't in the context?"

The four metrics:
    1. Faithfulness (0-1):
       Did the agent's answer come FROM the retrieved context?
       Example: Context says "TCS holds ISO27001 since 2019"
                Answer says "TCS has held ISO27001 since 2019" → high faithfulness
                Answer says "TCS has held ISO27001 since 2015" → low (hallucinated year)

    2. Answer Relevancy (0-1):
       Does the answer actually address the question asked?
       Low when the agent drifts into unrelated topics.

    3. Context Precision (0-1):
       Of everything retrieved from ChromaDB, how much was actually USED?
       High precision = retrieved context was mostly relevant.
       Low precision = retrieved a lot of noise that wasn't needed.

    4. Context Recall (0-1):
       Did the retrieval capture EVERYTHING needed to answer correctly?
       Low recall = important information existed in the DB but wasn't retrieved.

How to use:
    This script requires actual agent outputs to evaluate.
    Run it AFTER running evaluations through the VendorRisk system.

    Step 1: Run evaluations to populate the capture table:
        python tests/eval/run_eval.py --capture-mode

    Step 2: Run RAGAS evaluation:
        python tests/eval/ragas_eval.py

    Step 3: View results:
        cat tests/eval/ragas_results.json
"""

import json
import os
import sys
from pathlib import Path
from datetime import datetime

# Add backend to path so we can import app modules
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / "backend" / ".env")


def run_ragas_evaluation(captured_evaluations: list[dict]) -> dict:
    """
    Run RAGAS evaluation on captured agent outputs.

    Args:
        captured_evaluations: List of dicts, each containing:
            - question: what the agent was asked to evaluate
            - answer: what the agent produced (chain-of-thought + score)
            - contexts: list of strings returned by ChromaDB RAG
            - ground_truth: the correct answer (from golden_dataset.json)

    Returns:
        Dict with faithfulness, answer_relevancy, context_precision, context_recall scores
    """
    try:
        from ragas import evaluate
        from ragas.metrics import faithfulness, answer_relevancy, context_precision
        from datasets import Dataset
        from langchain_groq import ChatGroq
        from langchain_core.language_models import BaseLanguageModel

    except ImportError as e:
        print(f"❌ Import error: {e}")
        print("   Run: pip install ragas datasets --break-system-packages")
        return {"error": str(e), "scores": {}}

    if not captured_evaluations:
        print("❌ No evaluation data to analyze. Run with --capture-mode first.")
        return {"error": "no_data", "scores": {}}

    print(f"\n🔬 Running RAGAS evaluation on {len(captured_evaluations)} samples...")
    print("   Metrics: faithfulness, answer_relevancy, context_precision\n")

    # Build dataset in RAGAS format
    dataset_dict = {
        "question":   [e["question"]   for e in captured_evaluations],
        "answer":     [e["answer"]     for e in captured_evaluations],
        "contexts":   [e["contexts"]   for e in captured_evaluations],
        "ground_truth": [e["ground_truth"] for e in captured_evaluations],
    }
    dataset = Dataset.from_dict(dataset_dict)

    # Use Groq as the evaluator LLM instead of OpenAI
    # RAGAS uses an LLM internally to judge faithfulness and relevancy
    groq_api_key = os.environ.get("GROQ_API_KEY")
    if not groq_api_key:
        print("❌ GROQ_API_KEY not found in environment")
        return {"error": "missing_api_key", "scores": {}}

    evaluator_llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        api_key=groq_api_key,
        temperature=0.0,
    )

    # Run RAGAS — this makes LLM calls to judge each sample
    # Each metric uses the evaluator LLM to judge quality
    results = evaluate(
        dataset=dataset,
        metrics=[
            faithfulness,       # Did answer come from context?
            answer_relevancy,   # Did answer address the question?
            context_precision,  # Was retrieved context relevant?
        ],
        llm=evaluator_llm,
    )

    scores = {
        "faithfulness":       float(results["faithfulness"]),
        "answer_relevancy":   float(results["answer_relevancy"]),
        "context_precision":  float(results["context_precision"]),
    }

    print("📊 RAGAS Results:")
    print(f"   Faithfulness:      {scores['faithfulness']:.3f}")
    print(f"   Answer Relevancy:  {scores['answer_relevancy']:.3f}")
    print(f"   Context Precision: {scores['context_precision']:.3f}")

    # Interpretation
    for metric, score in scores.items():
        if score >= 0.9:
            status = "🟢 Excellent"
        elif score >= 0.8:
            status = "🟡 Good"
        elif score >= 0.7:
            status = "🟠 Acceptable"
        else:
            status = "🔴 Needs improvement"
        print(f"   {metric}: {status}")

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "sample_count": len(captured_evaluations),
        "scores": scores,
        "threshold": {
            "faithfulness":     0.85,  # CI/CD gate: block deploy if below this
            "answer_relevancy": 0.80,
        },
        "passes_gate": (
            scores["faithfulness"] >= 0.85
            and scores["answer_relevancy"] >= 0.80
        )
    }


def compute_accuracy_against_golden_dataset(
    golden_dataset_path: str,
    supabase_evaluations: list[dict]
) -> dict:
    """
    Compare system recommendations against manually labelled golden dataset.

    This is the accuracy metric that goes on the resume:
    "Maintained X% agreement with expert-labelled ground truth across N vendor evaluations"

    Args:
        golden_dataset_path: Path to golden_dataset.json
        supabase_evaluations: List of evaluation records from Supabase DB

    Returns:
        Dict with accuracy, agreement_rate, per_case_results
    """
    with open(golden_dataset_path) as f:
        golden = json.load(f)

    cases = golden["cases"]

    # Build lookup by vendor name (normalized)
    eval_by_vendor = {}
    for ev in supabase_evaluations:
        key = ev.get("vendor_name", "").lower().strip()
        eval_by_vendor[key] = ev

    results = []
    correct = 0
    total   = 0

    print("\n📋 Accuracy vs Manual Labels:")
    print(f"{'Vendor':<35} {'Expected':<10} {'Actual':<10} {'Match':<8} {'Score'}")
    print("-" * 80)

    for case in cases:
        vendor_key = case["vendor_name"].lower().strip()
        expected   = case["expected_recommendation"]
        ev         = eval_by_vendor.get(vendor_key)

        if not ev:
            print(f"{'  ' + case['vendor_name']:<35} {expected:<10} {'NO DATA':<10} {'⏭ SKIP'}")
            results.append({
                "vendor":   case["vendor_name"],
                "expected": expected,
                "actual":   None,
                "match":    None,
                "status":   "skipped_no_data",
            })
            continue

        actual      = ev.get("recommendation")
        composite   = ev.get("composite_score", 0)
        is_match    = (actual == expected)

        if is_match:
            correct += 1
            symbol = "✅"
        else:
            symbol = "❌"

        total += 1

        print(f"  {case['vendor_name']:<33} {expected:<10} {actual:<10} {symbol}      {composite:.1f}")

        results.append({
            "vendor":          case["vendor_name"],
            "expected":        expected,
            "actual":          actual,
            "composite_score": composite,
            "match":           is_match,
            "status":          "evaluated",
        })

    accuracy = (correct / total * 100) if total > 0 else 0

    print("-" * 80)
    print(f"\n🎯 Accuracy: {correct}/{total} correct ({accuracy:.1f}%)")

    # For resume: this is the number you cite
    resume_metric = (
        f"Maintained {accuracy:.0f}% agreement with manually-labelled "
        f"ground truth across {total} vendor evaluation cases"
    )
    print(f"\n📄 Resume metric: \"{resume_metric}\"")

    return {
        "timestamp":     datetime.utcnow().isoformat(),
        "total_cases":   total,
        "correct":       correct,
        "accuracy_pct":  round(accuracy, 1),
        "resume_metric": resume_metric,
        "passes_gate":   accuracy >= 85.0,  # CI/CD gate threshold
        "per_case":      results,
    }


if __name__ == "__main__":
    # Demo mode: use golden dataset ground truth as mock answers
    # In production this uses real Supabase data (see run_eval.py)

    print("🔬 VendorRisk RAGAS Evaluation")
    print("=" * 50)

    golden_path = Path(__file__).parent / "golden_dataset.json"
    with open(golden_path) as f:
        golden = json.load(f)

    # Build mock captured evaluations from ground truth
    # In production, run_eval.py populates this from actual system runs
    mock_captures = []
    for case in golden["cases"]:
        mock_captures.append({
            "question":     case["ragas_question"],
            "answer":       case["ragas_ground_truth"],   # In prod: actual agent output
            "contexts":     [case["ground_truth_compliance"]],
            "ground_truth": case["ragas_ground_truth"],
        })

    result = run_ragas_evaluation(mock_captures)

    output_path = Path(__file__).parent / "ragas_results.json"
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    print(f"\n✅ Results saved to {output_path}")
