LOGGING = True # whether or not log.md is generated
LOG_PROMPTS = True # logs prompts generated

MUNICODE_MUNIS = "src/config/municode_munis.json"

LOG_PATH = "log.md"


# model options
MODELS = {
    "thinker": "gemini-2.5-flash",
    "fast": "gemini-2.0-flash-lite"
}

RELEVANCE_THRESHOLD = 4 # completely arbitrary

SEARCH_TERM_LIMIT = 3 # only use the first 3 search terms