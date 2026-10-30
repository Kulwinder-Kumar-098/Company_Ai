from app.ingestion.parsers import extract_pdf_pages
from app.ingestion.chunking import chunk_pages
from app.ingestion.indexing import build_faiss_index, search_faiss

PDF_PATH = "data/raw/company_policies.pdf"

# 1. Extract PDF text
pages = extract_pdf_pages(PDF_PATH)
print(f"Pages extracted: {len(pages)}")

# 2. Split extracted text into chunks
chunks = chunk_pages(pages)
print(f"Chunks created: {len(chunks)}")

if not chunks:
    raise ValueError("No text chunks found in the PDF.")

# 3. Generate Ollama embeddings and build FAISS index
build_faiss_index(chunks)
print("FAISS index built successfully.")

# 4. Test retrieval
query = "What is the company's leave policy?"
results = search_faiss(query, top_k=3)

print(f"\nQuery: {query}")
for result in results:
    print(f"\nScore: {result['score']:.4f}")
    print(f"Source: {result['source']}, Page: {result['page_number']}")
    print(result["text"][:500])