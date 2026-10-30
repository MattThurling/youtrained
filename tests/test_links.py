from youtrained.links import (
    artist_id_from_path,
    artist_url,
    report_id_from_path,
    report_url,
    slug,
)

UC = "UCFtSXTlIMFFkyJbHO3V5b7A"


def test_slug_strips_accents_and_punctuation():
    assert slug("Beyoncé") == "beyonce"
    assert slug("Bonde do Forró") == "bonde-do-forro"
    assert slug('Bonnie "Prince" Billy') == "bonnie-prince-billy"
    assert slug("  Active Melody  ") == "active-melody"


def test_slug_empty_cases_and_cap():
    assert slug(None) == "" and slug("") == "" and slug("坂本龍一") == ""
    assert len(slug("word " * 50)) <= 60 and not slug("word " * 50).endswith("-")


def test_urls_fall_back_to_the_bare_id():
    assert artist_url(UC, "Johann Sebastian Bach") == f"/a/johann-sebastian-bach-{UC}"
    assert artist_url(UC, "坂本龍一") == f"/a/{UC}"
    assert report_url(f"yt_{UC}", "Active Melody") == f"/r/active-melody-yt_{UC}"
    assert report_url(f"yt_{UC}", None) == f"/r/yt_{UC}"


def test_paths_round_trip_and_accept_bare_ids():
    assert artist_id_from_path(artist_url(UC, "J.S. Bach")[3:]) == UC
    assert artist_id_from_path(UC) == UC
    assert artist_id_from_path("stale-slug-" + UC) == UC
    assert artist_id_from_path("nope") is None and artist_id_from_path("UCshort") is None
    dashed = "UCgyl5xVlLLUZVpgfMGw-ETA"
    assert artist_id_from_path(f"band-{dashed}") == dashed
    assert report_id_from_path(report_url(f"yt_{dashed}", "Some Band")[3:]) == f"yt_{dashed}"
    assert report_id_from_path("yt_someband") == "yt_someband"
    assert report_id_from_path("someband-yt_someband") == "yt_someband"
    assert report_id_from_path("nope") is None and report_id_from_path("yt_") is None
