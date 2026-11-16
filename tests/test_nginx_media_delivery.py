"""Tasks 5+8 (red): nginx media delivery must stream video correctly.

5. location /videos/ (and the thumbnail locations) must NOT buffer the
   whole object (proxy_buffering off + proxy_max_temp_file_size 0), must not
   strip/rewrite Content-Type or Content-Disposition, and must pass Range
   through to the storage and Accept-Ranges back to the browser (206).
8. X-Content-Type-Options must appear exactly ONCE on media responses
   (silo sends its own nosniff; nginx server-level add_header adds another).
   Fix: proxy_hide_header in every silo location. Public thumbnails get
   Cache-Control public, max-age=3600 (immutable per video id); the videos
   location must NOT get a long public cache (private, presigned).
"""

import re

from test_nginx_config import NGINX_TEMPLATE, _block, _text

SILO_LOCATIONS = ["location /videos/", "location /thumbnails/", "location /media/thumbnails/"]


class TestVideoStreaming:
    def test_videos_location_does_not_spool_whole_file(self) -> None:
        videos = _block(_text(), "location /videos/")
        assert re.search(r"proxy_buffering\s+off", videos), "video must stream, not buffer"
        assert re.search(r"proxy_max_temp_file_size\s+0", videos), "no temp-file spool"
        assert re.search(r"proxy_request_buffering\s+off", videos)

    def test_videos_location_passes_range_and_accept_ranges(self) -> None:
        videos = _block(_text(), "location /videos/")
        # Range reaches the storage; Accept-Ranges reaches the browser.
        assert re.search(r"proxy_set_header\s+Range\s+\$http_range", videos)
        assert re.search(r"proxy_pass_header\s+Accept-Ranges", videos)

    def test_videos_location_does_not_touch_content_headers(self) -> None:
        videos = _block(_text(), "location /videos/")
        # No rewriting/stripping of the type or disposition the presigned
        # URL asked for (response-content-type / response-content-disposition).
        assert "proxy_hide_header Content-Type" not in videos
        assert "proxy_hide_header Content-Disposition" not in videos
        assert not re.search(r"add_header\s+Content-Type", videos)
        assert not re.search(r"add_header\s+Content-Disposition", videos)

    def test_videos_location_has_no_long_public_cache(self) -> None:
        # Private bucket behind presigned URLs: no shared/public caching.
        videos = _block(_text(), "location /videos/")
        assert not re.search(r"add_header\s+Cache-Control\s+.*public", videos)


class TestNoDuplicateSecurityHeaders:
    def test_every_silo_location_hides_upstream_nosniff(self) -> None:
        text = _text()
        for loc in SILO_LOCATIONS:
            block = _block(text, loc)
            assert re.search(r"proxy_hide_header\s+X-Content-Type-Options", block), loc

    def test_server_level_security_headers_present(self) -> None:
        from test_nginx_config import _server_block

        server = _server_block(_text())
        assert re.search(r"add_header\s+X-Content-Type-Options\s+nosniff\s+always", server)
        assert re.search(r"add_header\s+X-Frame-Options\s+\S+\s+always", server)
        assert re.search(r"add_header\s+Referrer-Policy\s+\S+\s+always", server)

    def test_media_thumbnails_redeclare_headers_and_cache(self) -> None:
        # /media/thumbnails/ declares its own add_header (Cache-Control),
        # which cancels server-level inheritance — so the security headers
        # must be re-declared inside the block, exactly once each.
        media = _block(_text(), "location /media/thumbnails/")
        assert re.search(r'add_header\s+Cache-Control\s+"public,\s*max-age=3600"\s+always', media)
        assert len(re.findall(r"add_header\s+X-Content-Type-Options", media)) == 1
        assert len(re.findall(r"add_header\s+X-Frame-Options", media)) == 1
        assert len(re.findall(r"add_header\s+Referrer-Policy", media)) == 1

    def test_no_location_adds_headers_that_break_video_playback(self) -> None:
        # X-Frame-Options/Referrer-Policy do not affect <video> playback, but
        # a CSP/frame-ancestors or an add_header Content-* on media would.
        text = _text()
        for loc in SILO_LOCATIONS:
            block = _block(text, loc)
            assert "Content-Security-Policy" not in block
            assert not re.search(r"add_header\s+Content-Type", block)

    def test_whole_config_has_no_duplicate_nosniff_risk(self) -> None:
        # exactly one add_header X-Content-Type-Options per block: server
        # block + media location (which re-declares because of Cache-Control)
        text = _text()
        assert len(re.findall(r"add_header\s+X-Content-Type-Options", text)) == 2
        assert NGINX_TEMPLATE.exists()
