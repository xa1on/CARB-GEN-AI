SORTER_QUERY_TEMPLATE = """Query: "{query}".
Here is a comma seperated list of title/chapters/articles/sections:

[{name_list}]"""



RESPONSE_QUERY_TEMPLATE = """Answer the following question on the city/municipality of {muni_name} from the documents provided below for the muni/city of {muni_name}:

Below is the document in markdown format from the following link {muni_url}:

{text}



Question: {query}\n Response: """

GROUNDER_QUERY_TEMPLATE = """Is this answer accurate for the query "{query}" in regard to the city or municipality of {muni_name}?

Response:
"""

SEARCHER_QUERY_TEMPLATE = """Question: {query}, List of {n} search terms:"""

VERIFICATION_QUERY_TEMPLATE = """You previously answered this question about {muni_name}: Question: {query} Your answer: {original_response} Document: {context}
 Now, carefully review your answer:
1. Do those quotes actually support your conclusion?
2. Did you interpret the policy correctly?
 Respond with only:
- (VERIFIED) if your answer is correct and well-supported
 - (REJECTED) if you made an error or the evidence doesn't support your conclusion"""