"""Tests for sentiometer.stream parsing and statistics."""

from sentiometer.stream import StreamStats, parse_line


class TestParseLine:
    """Tests for the serial line parser."""

    def test_valid_line(self):
        raw = b"890206,758,735,4070,1063,572\r\n"
        result = parse_line(raw, expected_n=6)
        assert result == [890206.0, 758.0, 735.0, 4070.0, 1063.0, 572.0]

    def test_valid_line_no_crlf(self):
        raw = b"890206,758,735,4070,1063,572\n"
        result = parse_line(raw, expected_n=6)
        assert result == [890206.0, 758.0, 735.0, 4070.0, 1063.0, 572.0]

    def test_empty_line(self):
        assert parse_line(b"", expected_n=6) is None
        assert parse_line(b"\r\n", expected_n=6) is None

    def test_wrong_column_count(self):
        raw = b"890206,758,735,4070,1063\r\n"  # only 5 values
        assert parse_line(raw, expected_n=6) is None

    def test_non_numeric(self):
        raw = b"890206,abc,735,4070,1063,572\r\n"
        assert parse_line(raw, expected_n=6) is None

    def test_extra_whitespace(self):
        raw = b"  890206,758,735,4070,1063,572  \r\n"
        result = parse_line(raw, expected_n=6)
        assert result is not None
        assert result[0] == 890206.0

    def test_garbage_bytes(self):
        raw = b"\x00\xff\xfe"
        assert parse_line(raw, expected_n=6) is None


class TestStreamStats:
    """Tests for the statistics tracker."""

    def test_initial_state(self):
        stats = StreamStats()
        assert stats.samples_pushed == 0
        assert stats.dropped_samples == 0
        assert stats.parse_errors == 0

    def test_summary_format(self):
        stats = StreamStats()
        stats.samples_pushed = 5000
        stats.dropped_samples = 3
        stats.parse_errors = 1
        summary = stats.summary()
        assert "5,000" in summary
        assert "Dropped: 3" in summary

    def test_effective_rate(self):
        stats = StreamStats()
        stats.samples_pushed = 1000
        # Rate depends on elapsed time, just check it doesn't crash
        rate = stats.effective_rate
        assert isinstance(rate, float)
