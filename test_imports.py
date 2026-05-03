import time
import sys

def test_import(name):
    print(f"Importing {name}...", end="", flush=True)
    start = time.time()
    try:
        __import__(name)
        print(f" OK ({time.time() - start:.2f}s)")
    except Exception as e:
        print(f" FAILED: {e}")

test_import("fastapi")
test_import("uvicorn")
test_import("openai")
test_import("langchain_openai")
test_import("pinecone")
test_import("langchain_pinecone")
test_import("cohere")
