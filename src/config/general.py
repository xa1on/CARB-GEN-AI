LOGGING = True # whether or not log.md is generated
LOG_PROMPTS = True # logs prompts generated

MUNICODE_MUNIS = "src/config/municode_munis.json"

LOG_PATH = "log.md"

RELEVANCE_THRESHOLD = 4 # completely arbitrary (0-10)

SEARCH_TERM_LIMIT = 3 # only use the first n search terms

LLM_ATTEMPT_LIMIT = 5 # llm timeout after n failed attemps

LLM_ATTEMPT_DELAY = 10 # delay in seconds between llm call attempts