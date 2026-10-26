"""Whether a download fits — the arithmetic the pre-flight refuses on.

The refusal costs the user a download they asked for, so the cases that matter
are the boundary (exactly enough is enough) and every claim that must be counted
against the free space the store reported.
"""

from domain.disk_space import (
    NotEnoughSpaceToUnpack,
    bytes_reserved_by_others,
    disk_space_verdict,
    unpack_space_verdict,
    written_since_admitted,
)

_MB = 1024 * 1024
_HEADROOM = 100 * _MB


class TestDiskSpaceVerdict:
    def test_a_rom_that_fits_with_headroom_to_spare_fits(self):
        verdict = disk_space_verdict(file_size=500 * _MB, free_space=700 * _MB, reserved_bytes=0, multi_file=False)
        assert verdict.fits is True

    def test_the_headroom_is_required_on_top_of_the_rom(self):
        # 500 MB free for a 500 MB ROM is not enough: an emulator writes saves
        # and shader caches beside it.
        verdict = disk_space_verdict(file_size=500 * _MB, free_space=500 * _MB, reserved_bytes=0, multi_file=False)
        assert verdict.fits is False
        assert verdict.needed_bytes == 500 * _MB + _HEADROOM

    def test_exactly_enough_fits(self):
        verdict = disk_space_verdict(
            file_size=500 * _MB, free_space=500 * _MB + _HEADROOM, reserved_bytes=0, multi_file=False
        )
        assert verdict.fits is True

    def test_one_byte_short_does_not_fit(self):
        verdict = disk_space_verdict(
            file_size=500 * _MB, free_space=500 * _MB + _HEADROOM - 1, reserved_bytes=0, multi_file=False
        )
        assert verdict.fits is False

    def test_a_multi_file_rom_is_counted_twice(self):
        # It lands as a ZIP and is extracted beside it, so both exist at once.
        verdict = disk_space_verdict(file_size=500 * _MB, free_space=700 * _MB, reserved_bytes=0, multi_file=True)
        assert verdict.fits is False
        assert verdict.needed_bytes == 1000 * _MB + _HEADROOM

    def test_bytes_another_download_reserved_are_not_available(self):
        # Without this, two concurrent pre-flights each pass on space only one of
        # them fits (#1053).
        verdict = disk_space_verdict(
            file_size=500 * _MB, free_space=700 * _MB, reserved_bytes=300 * _MB, multi_file=False
        )
        assert verdict.fits is False

    def test_bytes_already_on_disk_are_not_needed_twice(self):
        # A resumed transfer only needs the remainder, so a near-complete resume
        # is not refused for the full size.
        verdict = disk_space_verdict(
            file_size=500 * _MB,
            free_space=200 * _MB,
            reserved_bytes=0,
            multi_file=False,
            already_on_disk=450 * _MB,
        )
        assert verdict.fits is True
        assert verdict.needed_bytes == 150 * _MB

    def test_more_on_disk_than_needed_never_goes_negative(self):
        verdict = disk_space_verdict(
            file_size=10 * _MB, free_space=0, reserved_bytes=0, multi_file=False, already_on_disk=10_000 * _MB
        )
        assert verdict.needed_bytes == 0
        assert verdict.fits is True

    def test_a_rom_of_unstated_size_always_fits(self):
        # Refusing on a number the server never sent would be a claim Tender
        # cannot make.
        verdict = disk_space_verdict(file_size=0, free_space=0, reserved_bytes=0, multi_file=False)
        assert verdict.fits is True

    def test_free_space_is_reported_after_the_reservations(self):
        verdict = disk_space_verdict(
            file_size=900 * _MB, free_space=700 * _MB, reserved_bytes=300 * _MB, multi_file=False
        )
        assert verdict.free_mb == 400

    def test_free_space_never_reads_as_negative(self):
        # Reservations can exceed what the store reports once a sibling is
        # mid-write; "-200MB free" is not a sentence to show anyone.
        verdict = disk_space_verdict(
            file_size=900 * _MB, free_space=100 * _MB, reserved_bytes=300 * _MB, multi_file=False
        )
        assert verdict.free_mb == 0

    def test_the_needed_figure_is_stated_in_the_unit_the_refusal_uses(self):
        verdict = disk_space_verdict(file_size=500 * _MB, free_space=0, reserved_bytes=0, multi_file=False)
        assert verdict.needed_mb == 600


class TestASingleZipThatWillBeUnpacked:
    def test_its_listed_contents_are_counted_with_it(self):
        verdict = disk_space_verdict(
            file_size=300 * _MB, free_space=699 * _MB, reserved_bytes=0, multi_file=False, unpacked_size=300 * _MB
        )
        assert verdict.fits is False
        assert verdict.needed_bytes == 600 * _MB + _HEADROOM

    def test_a_kept_zip_needs_only_its_contents(self):
        verdict = disk_space_verdict(
            file_size=300 * _MB,
            free_space=400 * _MB,
            reserved_bytes=0,
            multi_file=False,
            already_on_disk=300 * _MB,
            unpacked_size=300 * _MB,
        )
        assert verdict.fits is True
        assert verdict.needed_bytes == 300 * _MB + _HEADROOM


class TestUnpackSpaceVerdict:
    def test_the_contents_and_the_headroom_must_fit(self):
        verdict = unpack_space_verdict(unpacked_size=500 * _MB, free_space=600 * _MB, reserved_bytes=0)
        assert verdict.fits is True

    def test_one_byte_short_does_not_fit(self):
        verdict = unpack_space_verdict(unpacked_size=500 * _MB, free_space=600 * _MB - 1, reserved_bytes=0)
        assert verdict.fits is False

    def test_what_other_downloads_claimed_is_not_free(self):
        verdict = unpack_space_verdict(unpacked_size=500 * _MB, free_space=900 * _MB, reserved_bytes=400 * _MB)
        assert verdict.fits is False
        assert verdict.free_mb == 500

    def test_the_failure_states_both_figures(self):
        verdict = unpack_space_verdict(unpacked_size=500 * _MB, free_space=200 * _MB, reserved_bytes=0)
        assert str(NotEnoughSpaceToUnpack(verdict)) == "Not enough space to unpack (200MB free, need 600MB)"


class TestBytesReservedByOthers:
    def test_leaves_out_the_asking_downloads_own_claim(self):
        assert bytes_reserved_by_others({1: 100, 2: 300}, {}, 1) == 300

    def test_what_another_download_has_written_is_no_longer_claimed(self):
        entries = {2: {"status": "downloading", "_transferred": 120, "_on_disk_at_start": 20}}

        assert bytes_reserved_by_others({2: 300}, entries, 1) == 200

    def test_a_claim_never_counts_below_nothing(self):
        entries = {2: {"status": "downloading", "_transferred": 500}}

        assert bytes_reserved_by_others({2: 300}, entries, 1) == 0


class TestWrittenSinceAdmitted:
    def test_the_transfer_counts_beyond_what_was_on_disk_at_admission(self):
        assert written_since_admitted({"status": "downloading", "_transferred": 150, "_on_disk_at_start": 50}) == 100

    def test_what_an_extracting_download_has_unpacked_counts_too(self):
        entry = {"status": "extracting", "_transferred": 100, "bytes_downloaded": 40}

        assert written_since_admitted(entry) == 140

    def test_the_bytes_of_a_download_that_is_not_extracting_are_its_transfer(self):
        assert written_since_admitted({"status": "downloading", "_transferred": 100, "bytes_downloaded": 100}) == 100

    def test_an_entry_with_nothing_recorded_has_written_nothing(self):
        assert written_since_admitted({}) == 0
