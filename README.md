# xcipient

A Unix-style pipeline toolkit for searching FDA drug data. Queries DailyMed for drug labeling and inactive ingredients, filters by excipients, and cross-references NADAC to check if drugs are actively purchased by pharmacies.

Built for people who need to find drugs that don't contain specific inactive ingredients and verify they're actually available.

## Usage

```
drug search fluoxetine | drug ingredients | drug filter "propylene glycol" | drug ndcs | drug nadac --filter | drug fmt
```

## Commands

| Command | Description |
|---------|-------------|
| `drug search <name>` | Search DailyMed for drugs by name (supports partial match) |
| `drug ingredients` | Add inactive ingredients to drug records |
| `drug filter <excipient>` | Remove drugs containing specified excipient |
| `drug ndcs` | Add NDC (National Drug Code) identifiers |
| `drug nadac` | Check NADAC for pharmacy purchasing activity |
| `drug fmt` | Format output as summary table, CSV, or JSON |

Each command reads JSON from stdin and writes JSON to stdout (except `search` which takes a name argument, and `fmt` which outputs formatted text). Commands can be composed in any order via pipes.

## Examples

Search and filter:
```
drug search fluoxetine | drug ingredients | drug filter "propylene glycol" | drug fmt
```

Full pipeline with availability check:
```
drug search fluoxetine | drug ingredients | drug filter "propylene glycol" | drug ndcs | drug nadac --filter | drug fmt
```

Save intermediate results:
```
drug search fluoxetine > fluoxetine.json
cat fluoxetine.json | drug ingredients | drug filter lactose | drug fmt -f csv > results.csv
```

Verbose mode (progress on stderr):
```
drug search fluoxetine -v | drug ingredients -v | drug filter "propylene glycol" | drug fmt
```

## Options

Most commands support:
- `-v, --verbose` — Show progress on stderr
- `-h, --help` — Show help for that command

Additional options:
- `drug search -n 10` — Limit search results
- `drug filter --keep` — Invert filter (keep matching drugs)
- `drug nadac --filter` — Only output drugs found in NADAC
- `drug fmt -f csv|json|summary` — Output format (default: summary)

## Data Sources

- **DailyMed** (dailymed.nlm.nih.gov) — FDA drug labeling, inactive ingredients, NDC codes
- **NADAC** (data.medicaid.gov) — National Average Drug Acquisition Cost, indicating which drugs are actively purchased by pharmacies

## Setup

Requires Python 3.10+ and `requests`:

```
pip install requests
```

Add the repo directory to your PATH, then use `drug` (Linux/Mac) or `drug.bat` (Windows).

## Monolithic Script

`dailymed_search.py` is the original all-in-one script with built-in progress bars and CSV export. The pipeline tools in `drug.py` are the recommended interface.

```
python dailymed_search.py fluoxetine --exclude "propylene glycol" --available --csv results.csv
```
