LOGGING = True # whether or not log.md is generated
LOG_PROMPTS = True # logs prompts generated

MUNICODE_MUNIS = "src/config/municode_munis.json"

LOG_PATH = "log.md"

RELEVANCE_THRESHOLD = 2 # completely arbitrary #lowered it so I could test the ex. in main, when I lowered the 
                            # response and a good quote was found, maybe we can consider leaving this lower

SEARCH_TERM_LIMIT = 3 # only use the first 3 search terms

LLM_ATTEMPT_LIMIT = 5