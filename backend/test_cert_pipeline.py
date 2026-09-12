import asyncio
from app.services.cert_pipeline import discover_and_extract_certifications

async def main():
    result = await discover_and_extract_certifications("Infosys")
    print("\nSources checked:")
    for s in result["sources_checked"]:
        print(f"  - {s}")
    print("\nCertifications found:", result["certifications_found"])
    print("\nEvidence:")
    for e in result["evidence"]:
        print(f"  - {e}")
    print("\nUncertain:", result["uncertain"])
    print("Not mentioned:", result["explicitly_not_mentioned"])

asyncio.run(main())