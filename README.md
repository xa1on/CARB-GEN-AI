# CARB-GEN-AI

## Includes

- ```municode_scraper.py``` [municode](https://library.municode.com/) scraper
- ```chatbot.py``` full gemini 2.5 flash chatbot

## Installation

- Use pip to install dependencies

    ```sh
    pip install -r requirements.txt
    ```

- Set up .env file with ```GOOGLE``` environment variable containing your google cloud api key

## How to use

- ```python src/chatbot.py```
- go into ```main()``` and edit state, muni(municipality), and query to generate custom responses
    - generated log.md will show gemini thought process, responses, etc.