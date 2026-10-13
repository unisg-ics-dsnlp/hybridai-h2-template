"""Loader for datasets on data.sbb.ch via the Opendatasoft Explore API v2.1.

data.sbb.ch is a public open-data portal: no account or API key is required
to read any of its datasets (see https://help.opendatasoft.com/apis/ods-explore-v2/).

Two endpoints are used, because one of them cannot reach a whole dataset:

  * /records pages a dataset 100 rows at a time, and accepts ODSQL clauses and
    a row cap. It refuses an offset of 10,000 or more:

        offset=9900  -> HTTP 200
        offset=10000 -> HTTP 400 Bad Request

  * /exports streams a whole dataset in one response with no offset at all.

`load_dataset` picks between them on the dataset's own row count, so a caller
never has to think about the limit. Roughly a dozen datasets on the portal sit
above it -- ist-daten-sbb (70,314 rows), jahresformation (480,057) and
verkehrsperiode-jahresformation (64,525) among them.
"""

from __future__ import annotations

import requests

CATALOG_URL = "https://data.sbb.ch/api/explore/v2.1/catalog/datasets"
RECORDS_URL = CATALOG_URL + "/{dataset_id}/records"
EXPORTS_URL = CATALOG_URL + "/{dataset_id}/exports/json"
MAX_PAGE_SIZE = 100  # largest `limit` the records/catalog endpoints accept per request
REQUEST_TIMEOUT = 30

#: The /records endpoint rejects an offset at or beyond this value, so a
#: dataset larger than this cannot be paged to the end.
OFFSET_LIMIT = 10_000

#: A bulk export is one large response rather than many small ones;
#: ist-daten-sbb is roughly 20 MB of JSON and takes about 20 seconds.
EXPORT_TIMEOUT = 300


def _odsql_params(select, where, order_by, lang) -> dict:
    return {
        k: v for k, v in
        {"select": select, "where": where, "order_by": order_by, "lang": lang}.items()
        if v
    }


def _not_found(dataset_id: str) -> ValueError:
    return ValueError(
        f"no dataset {dataset_id!r} on data.sbb.ch "
        "(use the identifier from the dataset's Information tab, not its title)"
    )


def load_dataset(
    dataset_id: str,
    *,
    select: str | None = None,
    where: str | None = None,
    order_by: str | None = None,
    max_records: int | None = None,
    lang: str | None = None,
) -> list[dict]:
    """Fetch a data.sbb.ch dataset and return it as a list of row dicts.

    dataset_id is the identifier shown on the dataset's "Information" tab, e.g.
    for https://data.sbb.ch/explore/dataset/rail-traffic-information/information/
    it is "rail-traffic-information".

    select/where/order_by are passed through as ODSQL clauses to filter or shape
    the result server-side. max_records caps the number of rows fetched; leave it
    unset to fetch the whole dataset.

    Datasets that do not fit within the /records offset limit are fetched
    through the /exports endpoint instead. This is transparent: the return value
    has the same shape either way. Note that a server-side `where` clause is
    applied before the limit is considered, so filtering a large dataset down
    can keep it on the paging path.

    lang is a 2-letter language code (e.g. "de", "fr", "it", "en") used by the
    API for language-sensitive query features (such as date_format). It does
    NOT translate the actual field values: record content is whatever
    language the source dataset was published in, per field, regardless of
    lang.
    """
    url = RECORDS_URL.format(dataset_id=dataset_id)
    base_params = _odsql_params(select, where, order_by, lang)

    records: list[dict] = []
    offset = 0
    total_count: int | None = None

    while max_records is None or len(records) < max_records:
        limit = MAX_PAGE_SIZE if max_records is None else min(MAX_PAGE_SIZE, max_records - len(records))
        response = requests.get(
            url, params={**base_params, "limit": limit, "offset": offset}, timeout=REQUEST_TIMEOUT
        )
        if response.status_code == 404:
            raise _not_found(dataset_id)
        response.raise_for_status()
        payload = response.json()

        page = payload.get("results", [])
        records.extend(page)
        offset += len(page)
        total_count = payload.get("total_count", total_count)

        # The rows still wanted lie beyond the offset the endpoint will accept,
        # so switch to the export endpoint rather than walking into a 400.
        wanted = total_count if max_records is None else min(total_count or 0, max_records)
        if total_count is not None and wanted > OFFSET_LIMIT:
            exported = load_export(
                dataset_id, select=select, where=where, order_by=order_by, lang=lang
            )
            return exported[:max_records] if max_records is not None else exported

        if not page or (total_count is not None and offset >= total_count):
            break

    return records


def load_export(
    dataset_id: str,
    *,
    select: str | None = None,
    where: str | None = None,
    order_by: str | None = None,
    lang: str | None = None,
    timeout: int = EXPORT_TIMEOUT,
) -> list[dict]:
    """Fetch a whole dataset in one response, bypassing the offset limit.

    `load_dataset` calls this for you when a dataset is too large to page, so
    reach for it directly only when you want to force the bulk path. It accepts
    the same ODSQL clauses, so a server-side filter still applies, but it has
    no row cap -- use load_dataset(max_records=...) for that.
    """
    response = requests.get(
        EXPORTS_URL.format(dataset_id=dataset_id),
        params=_odsql_params(select, where, order_by, lang),
        timeout=timeout,
    )
    if response.status_code == 404:
        raise _not_found(dataset_id)
    response.raise_for_status()
    records = response.json()
    if not isinstance(records, list):
        raise ValueError(
            f"exports endpoint returned {type(records).__name__} for "
            f"{dataset_id!r}, expected a list of records"
        )
    return records


def list_datasets(*, lang: str | None = None) -> list[dict]:
    """List every dataset published on data.sbb.ch.

    Returns a list of {dataset_id, title, records_count} dicts. Use a value
    from the dataset_id column as the argument to load_dataset().

    records_count is also what decides whether load_dataset can page a dataset
    or has to export it; see OFFSET_LIMIT.

    lang is a 2-letter language code (e.g. "de", "fr", "it", "en") controlling
    which localized title/description is returned for each dataset. This only
    affects catalog metadata, not the language of a dataset's own record
    content. Not every dataset has metadata in every language; the portal
    falls back to its default when a translation is missing.
    """
    catalog_params = {"limit": MAX_PAGE_SIZE, "lang": lang} if lang else {"limit": MAX_PAGE_SIZE}
    rows: list[dict] = []
    offset = 0
    total_count: int | None = None

    while total_count is None or offset < total_count:
        response = requests.get(
            CATALOG_URL, params={**catalog_params, "offset": offset}, timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        payload = response.json()
        page = payload.get("results", [])
        total_count = payload.get("total_count", total_count)

        for item in page:
            default_meta = item.get("metas", {}).get("default", {})
            rows.append(
                {
                    "dataset_id": item.get("dataset_id"),
                    "title": default_meta.get("title"),
                    "records_count": default_meta.get("records_count"),
                }
            )
        offset += len(page)
        if not page:
            break

    return rows
