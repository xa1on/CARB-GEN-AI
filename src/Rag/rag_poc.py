"""
RAG Pipeline for CARB-GEN-AI
Retrieval-Augmented Generation for municipal ordinance documents

AUTHORS: Allen, BO
"""

import os
from dotenv import load_dotenv
import google.generativeai as genai

from langchain_community.document_loaders import UnstructuredMarkdownLoader # loads markdown files and converts into Langchain document
from langchain_text_splitters import RecursiveCharacterTextSplitter # splits long documents into smaller chunks (pargarphs, sentences - instead of mid word)
from langchain_community.embeddings import HuggingFaceEmbeddings  # Use old one
from langchain_google_genai import ChatGoogleGenerativeAI # langchain wrapper for gemini
from sklearn.metrics.pairwise import cosine_similarity

# Load environment variables
load_dotenv()


#Configure Gemini API
GOOGLE_API_KEY = os.getenv('GEMINI_FREE')
if not GOOGLE_API_KEY:
    raise ValueError("GEMINI_FREE not found in .env file!")

genai.configure(api_key=GOOGLE_API_KEY)
print("✓ Gemini API configured successfully!")



# -- PHASE 1: LOAD AND CHUNK DOCUMENT ---

# specific the markdown file path
file_path = "log.md" # Using chatbot output

# Check if the file exists
if not os.path.exists(file_path):
    print(f"❌ Error: File not found at: {file_path}")
    exit()

# load the document
print(f"\n Loading document from: {file_path}")
loader = UnstructuredMarkdownLoader(file_path)
documents = loader.load()
print(f" Loaded {len(documents)} document(s)")

# Chunk the text 
print( f"\n chunking document")
text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000, # Each chunk ~1000 characters
    chunk_overlap=150 # 150 characters overlap between chunks
)
chunks = text_splitter.split_documents(documents)
print(f" Split into {len(chunks)} chunks")

question = "Does Campbell have just cause eviction policies?"
print(f"Question: {question}")

# Create embeddings
print(f"\n Creating embeddings ...")
embeddings = HuggingFaceEmbeddings(
    model_name="all-MiniLM-L6-v2"
)
ind_vectors = embeddings.embed_documents([c.page_content for c in chunks])

query_vec = embeddings.embed_query(question)

# now use cosine similarity for custom, non-database-based comparisons
similarities = cosine_similarity([query_vec], ind_vectors)[0]
top_k = 3
top_indices = similarities.argsort()[-top_k:][::-1]

retrieved_chunks = [chunks[i] for i in top_indices]

# Create ChromaDB vector store
# print(f"\n Storing vectors in ChromaDB...")
# vectorstore = Chroma.from_documents(
#    documents=chunks,
#    embedding=embeddings,
#    persist_directory="./chroma_db" # Saves to disk!
#)
# print(f" Vector store created with {len(chunks)} chunks")


# --- PHASE 3: QUERY & ANSWER ---

# Create retriever from vector store
# print(f" Setting up retriever...")
# retriever = vectorstore.as_retriever(
#    search_kwargs={"k": 3} # Return top 3 most relevant chunkcs (can update this later )
# )

# Create Gemini LLM
print(f"\n Initializing Gemini...")
llm = ChatGoogleGenerativeAI(
    model="models/gemini-pro-latest",
    google_api_key = GOOGLE_API_KEY,
    temperature=0.3 # Lower= more deterministic answers
)

#Create RAG chain (combines retrieval + question answering)
# print(f"\n Building RAG chain...")
# qa_chain = RetrievalQA.from_chain_type(
#    llm=llm,
#    chain_type="stuff", # "stuff" = put all chunks in one prompt
#    retriever=retriever,
#    return_source_documents=True # Return which chunks were used
# )

context = "\n\n".join([c.page_content for c in retrieved_chunks])
# handbuild our new prmopt instead of just having pileline taking are of everything
prompt = f"""
Given the following municipal ordinance context to answer the question.

Context:
{context}

Question: {question}
"""
response = llm.invoke(prompt)
print("\n Answer:")
print(response)


# print(f"\n Searching vector database...")
# result = qa_chain.invoke({"query": question})

# print(f"\n Answer:")
# print(result["result"])

# print(f"\n Sources used: {len(result['source_documents'])} chunks")
# for i, doc in enumerate(result['source_documents']):
#    print(f"\nChunk {i+1} preview")
#    print(doc.page_content[:200]+ "...")

del chunks
del ind_vectors
del retrieved_chunks
    
