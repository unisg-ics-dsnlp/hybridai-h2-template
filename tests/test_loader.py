from unittest.mock import Mock, patch

import pytest

from data import loader


def _response(json_data, status_code=200):
    response = Mock()
    response.status_code = status_code
    response.json.return_value = json_data
    if status_code >= 400:
        response.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return response


def _records_page(start, count, total_count):
    return {
        "total_count": total_count,
        "results": [{"id": i} for i in range(start, start + count)],
    }


@patch("data.loader.requests.get")
def test_load_dataset_single_page(mock_get):
    mock_get.return_value = _response(_records_page(0, 3, 3))

    rows = loader.load_dataset("rail-traffic-information")

    assert len(rows) == 3
    assert [r["id"] for r in rows] == [0, 1, 2]
    mock_get.assert_called_once()
    url, kwargs = mock_get.call_args.args[0], mock_get.call_args.kwargs
    assert url == loader.RECORDS_URL.format(dataset_id="rail-traffic-information")
    assert kwargs["params"]["limit"] == loader.MAX_PAGE_SIZE
    assert kwargs["params"]["offset"] == 0


@patch("data.loader.requests.get")
def test_load_dataset_paginates_until_total_count(mock_get):
    mock_get.side_effect = [
        _response(_records_page(0, loader.MAX_PAGE_SIZE, loader.MAX_PAGE_SIZE + 50)),
        _response(_records_page(loader.MAX_PAGE_SIZE, 50, loader.MAX_PAGE_SIZE + 50)),
    ]

    rows = loader.load_dataset("rail-traffic-information")

    assert len(rows) == loader.MAX_PAGE_SIZE + 50
    assert mock_get.call_count == 2
    offsets = [call.kwargs["params"]["offset"] for call in mock_get.call_args_list]
    assert offsets == [0, loader.MAX_PAGE_SIZE]


@patch("data.loader.requests.get")
def test_load_dataset_stops_on_empty_page(mock_get):
    # total_count overstates what the server actually has left.
    mock_get.side_effect = [
        _response(_records_page(0, 3, 100)),
        _response({"total_count": 100, "results": []}),
    ]

    rows = loader.load_dataset("rail-traffic-information")

    assert len(rows) == 3
    assert mock_get.call_count == 2


@patch("data.loader.requests.get")
def test_load_dataset_max_records_caps_rows_and_requests(mock_get):
    mock_get.return_value = _response(_records_page(0, 30, 1000))

    rows = loader.load_dataset("rail-traffic-information", max_records=30)

    assert len(rows) == 30
    mock_get.assert_called_once()
    assert mock_get.call_args.kwargs["params"]["limit"] == 30


@patch("data.loader.requests.get")
def test_load_dataset_passes_odsql_and_lang_params(mock_get):
    mock_get.return_value = _response(_records_page(0, 1, 1))

    loader.load_dataset(
        "rail-traffic-information",
        select="title,startdatetime",
        where="type=\"2\"",
        order_by="startdatetime desc",
        lang="fr",
    )

    params = mock_get.call_args.kwargs["params"]
    assert params["select"] == "title,startdatetime"
    assert params["where"] == 'type="2"'
    assert params["order_by"] == "startdatetime desc"
    assert params["lang"] == "fr"


@patch("data.loader.requests.get")
def test_load_dataset_omits_unset_optional_params(mock_get):
    mock_get.return_value = _response(_records_page(0, 1, 1))

    loader.load_dataset("rail-traffic-information")

    params = mock_get.call_args.kwargs["params"]
    assert "select" not in params
    assert "where" not in params
    assert "order_by" not in params
    assert "lang" not in params


@patch("data.loader.requests.get")
def test_load_dataset_unknown_id_raises_value_error(mock_get):
    mock_get.return_value = _response({}, status_code=404)

    with pytest.raises(ValueError, match="does-not-exist"):
        loader.load_dataset("does-not-exist")


@patch("data.loader.requests.get")
def test_load_dataset_propagates_other_http_errors(mock_get):
    mock_get.return_value = _response({}, status_code=500)

    with pytest.raises(Exception, match="HTTP 500"):
        loader.load_dataset("rail-traffic-information")


@patch("data.loader.requests.get")
def test_load_dataset_exports_when_dataset_exceeds_offset_limit(mock_get):
    """A dataset larger than the offset limit cannot be paged to the end.

    /records refuses offset >= 10000 with HTTP 400, so load_dataset must switch
    to /exports rather than walking into that error. ist-daten-sbb (70,314
    rows) is the case this protects.
    """
    big = loader.OFFSET_LIMIT + 5_000
    mock_get.side_effect = [
        _response(_records_page(0, loader.MAX_PAGE_SIZE, big)),
        _response([{"id": i} for i in range(big)]),  # /exports returns a list
    ]

    rows = loader.load_dataset("ist-daten-sbb")

    assert len(rows) == big
    assert mock_get.call_count == 2
    # The second call must be the exports endpoint, with no offset.
    export_url = mock_get.call_args_list[1].args[0]
    assert export_url == loader.EXPORTS_URL.format(dataset_id="ist-daten-sbb")
    assert "offset" not in mock_get.call_args_list[1].kwargs["params"]


@patch("data.loader.requests.get")
def test_load_dataset_keeps_paging_when_dataset_fits(mock_get):
    # Just inside the limit: no reason to pull a whole export.
    total = loader.OFFSET_LIMIT
    mock_get.side_effect = [
        _response(_records_page(i, loader.MAX_PAGE_SIZE, total))
        for i in range(0, total, loader.MAX_PAGE_SIZE)
    ]

    rows = loader.load_dataset("rail-traffic-information")

    assert len(rows) == total
    assert all(
        call.args[0] == loader.RECORDS_URL.format(dataset_id="rail-traffic-information")
        for call in mock_get.call_args_list
    )


@patch("data.loader.requests.get")
def test_load_dataset_max_records_below_limit_avoids_the_export(mock_get):
    # A small sample of a huge dataset stays on the cheap paging path.
    mock_get.return_value = _response(_records_page(0, 50, 500_000))

    rows = loader.load_dataset("jahresformation", max_records=50)

    assert len(rows) == 50
    mock_get.assert_called_once()
    assert mock_get.call_args.args[0] == loader.RECORDS_URL.format(
        dataset_id="jahresformation"
    )


@patch("data.loader.requests.get")
def test_load_dataset_truncates_an_export_to_max_records(mock_get):
    big = loader.OFFSET_LIMIT + 5_000
    wanted = loader.OFFSET_LIMIT + 100
    mock_get.side_effect = [
        _response(_records_page(0, loader.MAX_PAGE_SIZE, big)),
        _response([{"id": i} for i in range(big)]),
    ]

    rows = loader.load_dataset("ist-daten-sbb", max_records=wanted)

    assert len(rows) == wanted


@patch("data.loader.requests.get")
def test_load_export_passes_odsql_and_omits_unset_params(mock_get):
    mock_get.return_value = _response([{"id": 1}])

    loader.load_export("wifistation", where='isocountrycode:"CH"')

    params = mock_get.call_args.kwargs["params"]
    assert params["where"] == 'isocountrycode:"CH"'
    assert "select" not in params
    assert "order_by" not in params
    assert mock_get.call_args.kwargs["timeout"] == loader.EXPORT_TIMEOUT


@patch("data.loader.requests.get")
def test_load_export_unknown_id_raises_value_error(mock_get):
    mock_get.return_value = _response({}, status_code=404)

    with pytest.raises(ValueError, match="does-not-exist"):
        loader.load_export("does-not-exist")


@patch("data.loader.requests.get")
def test_load_export_rejects_a_non_list_payload(mock_get):
    # The endpoint should hand back a list of records; anything else means the
    # response was not what we asked for, and must not become an empty frame.
    mock_get.return_value = _response({"error": "nope"})

    with pytest.raises(ValueError, match="expected a list"):
        loader.load_export("wifistation")


def _catalog_item(dataset_id, title, records_count):
    return {
        "dataset_id": dataset_id,
        "metas": {"default": {"title": title, "records_count": records_count}},
    }


@patch("data.loader.requests.get")
def test_list_datasets_extracts_id_title_records_count(mock_get):
    mock_get.return_value = _response(
        {
            "total_count": 2,
            "results": [
                _catalog_item("rail-traffic-information", "Rail traffic information", 6796),
                _catalog_item("tunnel", "Tunnels", 289),
            ],
        }
    )

    rows = loader.list_datasets()

    assert [r["dataset_id"] for r in rows] == ["rail-traffic-information", "tunnel"]
    assert [r["title"] for r in rows] == ["Rail traffic information", "Tunnels"]
    assert [r["records_count"] for r in rows] == [6796, 289]


@patch("data.loader.requests.get")
def test_list_datasets_paginates(mock_get):
    page1 = {
        "total_count": loader.MAX_PAGE_SIZE + 1,
        "results": [_catalog_item(f"id-{i}", f"title-{i}", i) for i in range(loader.MAX_PAGE_SIZE)],
    }
    page2 = {
        "total_count": loader.MAX_PAGE_SIZE + 1,
        "results": [_catalog_item("id-last", "title-last", 1)],
    }
    mock_get.side_effect = [_response(page1), _response(page2)]

    rows = loader.list_datasets()

    assert len(rows) == loader.MAX_PAGE_SIZE + 1
    assert mock_get.call_count == 2


@patch("data.loader.requests.get")
def test_list_datasets_passes_lang_param(mock_get):
    mock_get.return_value = _response({"total_count": 0, "results": []})

    loader.list_datasets(lang="de")

    assert mock_get.call_args.kwargs["params"]["lang"] == "de"


@patch("data.loader.requests.get")
def test_list_datasets_omits_lang_param_when_unset(mock_get):
    mock_get.return_value = _response({"total_count": 0, "results": []})

    loader.list_datasets()

    assert "lang" not in mock_get.call_args.kwargs["params"]
