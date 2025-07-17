
import os
import json
import csv
from chatbot import *
from dotenv import load_dotenv
from google import genai

load_dotenv()
GOOGLE_API_KEY = os.getenv('GOOGLE') # google cloud api key

REFERENCE = "src/data/8 cities 3 policies.csv"
QUERIES = "src/data/QUERIES.json"
RESULT = "src/data/result.csv"
LOGS = "logs/"

def batch(client, muni_nav, reference, queries, result, logs):
    data = open(queries)
    query_ref = json.load(data)
    data.close()

    field_names = []

    answers = []

    with open(reference, mode='r') as csv_file:
        csv_reader = csv.reader(csv_file)
        for line in csv_reader:
            if not field_names:
                field_names = line
                with open(result, mode='w') as csv_result: # reset file
                    csv_result.write(','.join(field_names) + '\n')
                continue
            city, policy_type, _ = line
            if not os.path.exists(f"{logs}{city}"):
                os.makedirs(f"{logs}{city}")
            filename = f"""{city}/{policy_type}_log.md"""
            _, _, structured_response = init(muni_nav, "california", city.lower(), query_ref[policy_type], client)
            print(structured_response)
            answer = [city, policy_type, 'Y' if structured_response and (structured_response["binary_response"]) else 'N']
            answers.append(answer)
            print(answers)
            with open(result, mode='a') as csv_file: # incremental updates
                csv_file.write(','.join(answer) + '\n')
            os.replace("log.md", logs + filename) # save log
        return answers, list(csv_reader)

def evaluate(results, reference):
    field_names = []
    total = 0
    correct = 0
    index = 0
    fp = 0
    fn = 0
    tp = 0
    tn = 0
    policy_types = {}
    for result in results:
        ref_city, ref_policy_type, ref_response = reference[index]
        city, policy_type, response = result
        if ref_city != city or ref_policy_type != policy_type:
            assert f"MISMATCH: REFERENCE: {reference[index]} -> ANSWER: {result}"
        
        if not field_names:
            field_names = result
        else:
            if not policy_type in policy_types:
                policy_types[policy_type] = {
                    "total": 0,
                    "correct": 0,
                    "errors": {
                        "tp": 0,
                        "tn": 0,
                        "fp": 0,
                        "fn": 0
                    }
                }
            policy = policy_types[policy_type]
            policy["total"] += 1
            if ref_response == response:
                policy["correct"] += 1
                correct += 1
                if ref_response == "Y":
                    policy["errors"]["tp"] += 1
                    tp += 1
                else:
                    policy["errors"]["tn"] += 1
                    tn += 1
            else:
                if ref_response == "Y":
                    policy["errors"]["fn"] += 1
                    fn += 1
                else:
                    policy["errors"]["fp"] += 1
                    fp += 1
            total += 1
        index += 1
    print(f"total: {total}, correct: {correct}, accuracy: {correct / total}, tp: {tp}, tn: {tn}, fn: {fn}, fp: {fp}")

    for policy, result in policy_types.items():
        print(f"{policy}: {result}")
        
    return {
        "total": total,
        "correct": correct,
        "errors": {
            "tp": tp,
            "tn": tn,
            "fp": fp,
            "fn": fn
        },
        "indiv_policies": policy_types
    }


def main():
    client = genai.Client(api_key=GOOGLE_API_KEY)
    municode_nav = municode.MuniCodeCrawler()

    answer, reference = batch(client, municode_nav, REFERENCE, QUERIES, RESULT, LOGS)
    evaluate(answer, reference)
            
def run_eval():
    with open(RESULT, mode='r') as result_file:
        with open(REFERENCE, mode='r') as reference_file:
            result = csv.reader(result_file)
            reference = csv.reader(reference_file)
            evaluate(list(result), list(reference))

if __name__ == "__main__":
    main()