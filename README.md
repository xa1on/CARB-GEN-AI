# CARB-GEN-AI

## Includes

- ```municode_scraper.py``` [municode](https://library.municode.com/) scraper
- ```chatbot.py``` full gemini 2.5 flash chatbot

## Installation

- Use pip to install dependencies

    ```sh
    pip install -r requirements.txt
    ```

- Set up .env file with ```GEMINI_PAID``` and ```GEMINI_FREE``` environment variable containing your google cloud api keys

## How to use

- ```python src/chatbot.py```
  - go into ```main()``` and edit state, muni(municipality), and query to generate custom responses
    - generated log.md will show gemini thought process, responses, etc.

- ```python src/batch_test.py```
  - setup `queries.json` to map policies to prompts
  - setup a reference csv as an answer guide
  - run the file and it'll generate a result csv

- ```python src/link_test.py```
  - find broken links in a an input csv
    - you can export the google spreadsheet as a csv through file -> download -> csv
  - run the file and it'll generate a text file containing all the broken links it finds into `data/result`