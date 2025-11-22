# utils.py
import re

def simple_sentence_split(text):
    s = re.split(r'(?<=[.!?])\s+', text.strip())
    return [x.strip() for x in s if x.strip()]

def first_n_sentences(text, n=2):
    sents = simple_sentence_split(text)
    return " ".join(sents[:n]).strip()
