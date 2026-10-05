"""Explicit real-browser regression; no paid APIs or remote site."""
import argparse
from pathlib import Path
from urllib.parse import quote

from jev_ultrafast.browser import Browser


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', default=str(Path(__file__).parents[1] / 'jev_ultrafast/snapshot.js'))
    args = parser.parse_args()
    source = Path(args.snapshot).read_text(encoding='utf-8')
    html = '''<title>Toggle token regression</title>
    <label><input id="box" type="checkbox" value="true">Public</label>
    <label><input type="radio" name="mode" value="public">Mode Public</label>
    <label><input type="radio" name="mode" value="private" checked>Mode Private</label>
    <input type="radio" name="unnamed" value="option-A">
    <input type="radio" name="defaults">
    <label><input type="radio" name="duplicate" value="one">Option</label>
    <label><input type="radio" name="duplicate" value="two">Option</label>
    <button role="switch" aria-checked="false">Switch</button>
    <label>Text<input type="text" value="true"></label>'''
    browser = Browser('data:text/html,' + quote(html))
    try:
        page = browser.evaluate(source)
        by_label = {a['label']: a for a in page['actions']}
        assert by_label['Public']['value'] == by_label['Public']['checked'] == 'false'
        assert by_label['Mode Public']['value'] == 'false'
        assert by_label['Mode Private']['value'] == 'true'
        assert by_label['Switch']['value'] == 'false'
        assert by_label['Text']['value'] == 'true'
        assert by_label['radio (option-A)']['value'] == 'false'
        assert by_label['radio']['value'] == 'false'
        duplicates=[a for a in page['actions'] if a['label']=='Option']
        assert [a['submission_value'] for a in duplicates] == ['one','two']
        assert all(a['value']=='false' for a in duplicates)
        browser.evaluate("document.getElementById('box').click()")
        after = {a['label']: a for a in browser.evaluate(source)['actions']}
        assert after['Public']['value'] == 'true'
        assert browser.evaluate("document.getElementById('box').value") == 'true'
    finally:
        browser.close()
    print('PASS: bool states are independent of submission tokens; text values preserved')


if __name__ == "__main__":
    main()
