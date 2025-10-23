import os
from pathlib import Path
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sentence_transformers import SentenceTransformer
import chromadb
from google import genai

EMBEDDING_MODEL = "all-MiniLM-L6-v2"
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 100
CHROMA_DIR = "./chroma_db" 
genai_client = genai.Client(api_key=os.environ.get("some_API_KEY_LOWKEY_TRYNA_FIGURE_IT_OUT"))

# loading and doing documents into chunks
def load_documents(input_dir: str):
    docs = []
    for p in Path(input_dir).rglob("*.txt"):
        with open(p, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
        if text.strip():
            docs.append({"source": str(p), "text": text})
    return docs

def chunk_documents(docs):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", " ", ""],
    )
    chunks = []
    for d in docs:
        for i, chunk in enumerate(splitter.split_text(d["text"])):
            chunks.append({"id": f"{d['source']}_{i}", "text": chunk, "source": d["source"]})
    return chunks

# Embed using pre-defined functions and store in ChromaDB (like Zack mentioned)
def build_chroma_index(chunks):
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    collection = client.get_or_create_collection(name="rag_collection")
    model = SentenceTransformer(EMBEDDING_MODEL)

    texts = [c["text"] for c in chunks]
    ids = [c["id"] for c in chunks]
    metas = [{"source": c["source"]} for c in chunks]

    print(f"[INFO] Embedding {len(texts)} chunks...")
    embeddings = model.encode(texts, normalize_embeddings=True).tolist()

    collection.add(documents=texts, embeddings=embeddings, metadatas=metas, ids=ids)
    print(f"[OK] Stored {len(texts)} chunks in ChromaDB at {CHROMA_DIR}")

# anticipate to be very long code, passed for now, will dictate the retrieval stage
def query_chroma():
  pass


