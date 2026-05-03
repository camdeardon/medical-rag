# pubmed_ingest.py
import os
import requests
import time
from pathlib import Path
from xml.etree import ElementTree as ET

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

print(os.environ.get("OPENAI_API_KEY"))