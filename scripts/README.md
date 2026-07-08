# Scripts

## `llms_full_to_markdown.py`

Convert an `llms-full.txt` file (or URL) that may contain MDX/JSX and embedded HTML into cleaner Markdown-style output.

### Requirements

- Python 3.11+

### Usage

From the repository root:

```bash
python scripts/llms_full_to_markdown.py <source>
```

- `<source>` can be:
  - a local file path, or
  - an `http://` / `https://` URL.

Write output to a file:

```bash
python scripts/llms_full_to_markdown.py <source> -o output.md
```

### Examples

Local input:

```bash
python scripts/llms_full_to_markdown.py ./llms-full.txt -o cleaned.md
```

Remote input:

```bash
python scripts/llms_full_to_markdown.py https://modelcontextprotocol.io/llms-full.txt -o mcp-cleaned.md
```

### What it does

- removes common MDX wrapper noise (such as import/export lines)
- keeps fenced code blocks intact
- extracts readable text from embedded HTML
- converts HTML list items into markdown bullet points

### Notes

- If remote URL fetch fails in restricted environments (proxy/firewall), download the file locally first and run the script on the local path.

## `build_top20_packs.py`

Acceptance harness for the general web crawler: builds `.ctx` packs for 19 of
the top-20 most intentionally installed Python packages (pydantic from its
`llms.txt`, the rest crawled from their docs roots — see
`docs/top20-python-packages.md`), verifies each with `synd verify`, and prints
a coverage report. boto3 is excluded by scope decision; its subtree recipe is
printed at the end of the run.

```bash
python scripts/build_top20_packs.py                 # everything (network, slow)
python scripts/build_top20_packs.py --only requests # one package
```

Already-built packs in `packs/` are skipped, so re-running after a partial
build is safe.
