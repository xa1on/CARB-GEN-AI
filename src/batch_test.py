
import os
import json
import csv
from chatbot import *
from dotenv import load_dotenv
from google import genai

load_dotenv()
GOOGLE_API_KEY = os.getenv('GOOGLE') # google cloud api key

REFERENCE = "src/data/8 cities 3 policies.csv"
QUERYS = "src/data/querys.json"
RESULT = "src/data/result.csv"
LOGS = "logs/"

def main():
    client = genai.Client(api_key=GOOGLE_API_KEY)
    municode_nav = municode.MuniCodeCrawler()

    data = open(QUERYS)
    query_ref = json.load(data)
    data.close()

    field_names = []

    result = []

    with open(REFERENCE, mode='r') as csv_file:
        csv_reader = csv.reader(csv_file)
        for line in csv_reader:
            if not field_names:
                field_names = line
                continue
            city, policy_type, _ = line
            if not os.path.exists(f"{LOGS}{city}"):
                os.makedirs(f"{LOGS}{city}")
            filename = f"""{city}/{policy_type}_log.md"""
            _, _, structured_response = init(municode_nav, "california", city.lower(), query_ref[policy_type], client)
            print(structured_response)
            result.append([city, policy_type, 'Y' if structured_response and (structured_response["binary_response"]) else 'N'])
            print(result)
            with open(RESULT, mode='w') as csv_file:
                csv_writer = csv.writer(csv_file)
                csv_writer.writerow(field_names)
                csv_writer.writerows(result)
            os.replace("log.md", LOGS + filename)
            


if __name__ == "__main__":
    main()