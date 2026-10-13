# Data access

[Project overview](../README.md) · [Ontology mappings](ontology.md)

[data/loader.py](../src/data/loader.py) reads public datasets from
[data.sbb.ch](https://data.sbb.ch) through Explore API v2.1. It returns a list
of row dictionaries. Public downloads need no account or API key; the Python
dependency is `requests`.

## Discover datasets

Use the identifier from the portal URL or Information tab, rather than the
display title. Query the live catalog instead of relying on a dated inventory:

```python
from data import list_datasets

catalog = list_datasets(lang="en")
for item in sorted(catalog, key=lambda item: item["dataset_id"]):
    print(item["dataset_id"], item["title"], item["records_count"])
```

The catalog lists published datasets. The CLI's `--datasets` lists only
sources supported by the ontology registry.

## Load records

```python
from data import load_dataset, load_export

rows = load_dataset("wifistation")
sample = load_dataset("ist-daten-sbb", max_records=20)
chur = load_dataset("ist-daten-sbb", where='bpuic:"8509000"')

# Force a bulk download when the whole dataset is needed.
service = load_export("ist-daten-sbb")
```

`load_dataset()` fetches all rows unless given `max_records`. It pages
through `/records` in batches of at most 100 and switches to a JSON export
when requested records exceed the 10000-record offset window. Both paths
return the same Python shape. Exports are parsed into memory; the loader is
not a streaming iterator. Row counts and download times depend on the feed.

| Argument | Meaning |
|---|---|
| `dataset_id` | Portal dataset identifier |
| `select` | ODSQL fields or expressions |
| `where` | Server-side ODSQL filter |
| `order_by` | ODSQL sorting |
| `max_records` | Row cap for `load_dataset()`; default all rows |
| `lang` | Metadata/query language, such as `"de"` or `"en"` |

`load_export()` accepts filtering, sorting, selection, and language options
plus `timeout`, but no `max_records`. Page requests default to 30 seconds;
exports default to 300 seconds.

## Filters and stable pagination

ODSQL supports exact equality with `=`; it is not generally invalid.
See the official [text-comparison reference](https://help.opendatasoft.com/apis/ods-explore-v2/#section/Text-comparison-operators).

Both `isocountrycode="CH"` and `isocountrycode:"CH"` were checked against
the SBB registry API. Its `stoppoint` field is text: compare it with a quoted
`"true"` value rather than a boolean literal.

```python
from data import load_dataset

stations = load_dataset(
    "dienststellen-gemass-opentransportdataswiss",
    where='isocountrycode:"CH" and stoppoint:"true" and search(meansoftransport,"TRAIN")',
    order_by="number",
)
```

Use a unique, stable ordering key when paging. The builder orders registry
downloads by `number` and rejects duplicate normalized IDs to catch overlapping
pages. Sorting does not freeze a dataset that changes during download.

`lang` affects metadata and language-sensitive query features; it does not
translate source field values. An unknown dataset raises `ValueError`;
other HTTP and request errors propagate.

## Source-data quirks

Station IDs can be integers, float-shaped numbers, or strings. Some line
datasets carry five-digit Didok numbers. Booleans can be strings, transport
modes can be pipe-delimited, and coordinates can be dictionaries. Missing IDs
occur, and some facility inventories provide only names. Ontology ID and
mapping helpers normalize these shapes and reject ambiguous name matches.

`ist-daten-sbb` supplies previous-day reports. Facility inventories and yearly
passenger observations have their own update periods. Retrieval dates and
service operating days are different concepts; interpret answers against the
loaded snapshot.

## References

- [Explore API and ODSQL](https://help.opendatasoft.com/apis/ods-explore-v2/)
- [SBB dataset catalog](https://data.sbb.ch/explore/)
- [SBB data-use terms](https://data.sbb.ch/page/licence)
