# Documents skill

Handle PDF, DOCX, and XLSX files: download them with `download_file`, extract
their content with `parse_document`, and produce output files with `write_file`.

Rules:

- `download_file(url, filename)` saves into the task workspace; filenames are
  plain names (no paths).
- `parse_document(filename)` extracts text: PDFs page by page, DOCX paragraphs
  and tables, XLSX sheets as rows. Large documents are truncated — work with
  the excerpt you get.
- Produced artifacts (summaries, CSVs, notes) go in the workspace via
  `write_file`; mention every file you create in the final answer.
- If parsing reports a missing library, tell the user which extra to install
  (`pip install -e .[documents]`) instead of guessing the content.
