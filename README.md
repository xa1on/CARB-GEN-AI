# CARB-GEN-AI

## Includes

- ```municode_scraper.py``` [municode](https://library.municode.com/) scraper
- ```chatbot.py``` full gemini 2.5 flash chatbot
- ```link_test``` check for broken links in csv
- ```check_updates``` check for updates for municode links in csv

## Installation

- Use pip to install dependencies

    ```sh
    pip install -r requirements.txt
    ```

- Set up .env file with ```GEMINI_PAID``` and ```GEMINI_FREE``` environment variable containing your google cloud api keys (for chatbot)

## How to use

- ```python -m src.chatbot```
  - go into ```main()``` and edit state, muni(municipality), and query to generate custom responses
    - generated log.md will show gemini thought process, responses, etc.

- ```python -m src.batch_test```
  - setup `queries.json` to map policies to prompts
  - setup a reference csv as an answer guide
  - run the file and it'll generate a result csv

- ```python -m src.link_test```
  - get an input csv continaing the link data
    - you can export the data as a csv through our website or through through google sheets
  - set `CSV_FILE` to the csv file path
  - run the file and it'll generate a text file containing all the broken links it finds into path specified by `LOG_FILE`

- ```python -m src.check_updates```
  - get an input csv containing link data
    - you can export the data as a csv through our website or through through google sheets
  - set `CSV_FILE` to the csv file path
  - run the file and it'll generate a text file containing all the municipalities that require update into path specified by `OUTPUT_FILE`