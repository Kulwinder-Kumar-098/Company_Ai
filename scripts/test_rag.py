
from app.services.knowledge_service import answer_question

result = answer_question(
    "What is the company's leave policy?"
)

print("\nAnswer:")
print(result["answer"])

print("\nSources:")
for source in result["sources"]:
    print(
        f"{source['source']} | "
        f"Page {source['page_number']} | "
        f"Score: {source['score']:.4f}"
    )
