import scrapers.municode_scraper
import os
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
GOOGLE_API_KEY = os.getenv('GOOGLE')


def main():
    client = genai.Client(api_key=GOOGLE_API_KEY)
    while(True):
        print("----------------------------")
        query = input("input: ")
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=query,
            config=types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(
                    include_thoughts=True
                )
            )
        )
        for part in response.candidates[0].content.parts:
            if not part.text:
                continue
            if part.thought:
                print(f"think: {part.text}\n")
            else:
                print(f"answer: {part.text}\n")

if __name__ == "__main__":
    main()