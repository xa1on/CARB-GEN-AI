SORTER_QUERY_TEMPLATE = """Query: "{query}".
Here is a comma seperated list of title/chapters/articles/sections:

{name_list}"""



RESPONSE_QUERY_TEMPLATE = """Answer the following question on the city/municipality of {muni} from the documents provided below for the muni/city of {muni}:

Below is the document in markdown format from the following link {muni_code_url}:

{text}



Question: {query}\n Response: """

GROUNDER_QUERY_TEMPLATE = """Is this answer accurate for the query "{query}" in regard to the city or municipality of {muni}?

Response:
"""

SEARCHER_QUERY_TEMPLATE = """Question: {query}, List of {n} search terms:"""