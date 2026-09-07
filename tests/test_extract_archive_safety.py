import struct

import pytest

from extract import (
    ArchiveValidationError,
    KsPck,
    KsPckFile,
)


def _entry(path, *, file_hash=1, size=0, offset=0, flags=0):
    encoded = path.encode("utf-8")
    return struct.pack(
        "<224sIHHQQQ",
        encoded.ljust(224, b"\0"),
        0,
        flags,
        len(encoded),
        file_hash,
        size,
        offset,
    )


def _package(tmp_path, entries, data=b""):
    table = b"".join(entries) + bytes(256)
    archive = tmp_path / "content.kspkg"
    archive.write_bytes(data + table)
    package = KsPck(archive)
    package.FILE_TBL_SZ = len(table)
    package.parse_file_tbl()
    return package


@pytest.mark.parametrize(
    "path",
    [
        "../outside.txt",
        r"..\outside.txt",
        "nested/../../outside.txt",
        r"nested\..\outside.txt",
        "/absolute.txt",
        r"\absolute.txt",
        r"C:\absolute.txt",
        r"\\server\share\file.txt",
        "nested/child\\file.txt",
        "bad\x00name.txt",
        "CON.txt",
        "nested/NUL",
        "nested/LPT1.log",
    ],
)
def test_rejects_unsafe_member_paths(tmp_path, path):
    with pytest.raises(ArchiveValidationError):
        _package(tmp_path, [_entry(path)])


def test_allows_benign_nesting_and_directory_entries(tmp_path):
    data = b"hello"
    package = _package(
        tmp_path,
        [
            _entry("nested", file_hash=1, flags=int(KsPckFile.FileFlags.Directory)),
            _entry("nested/file.txt", file_hash=2, size=len(data), offset=0),
        ],
        data,
    )
    output = tmp_path / "out"
    package.extract_all(output)
    assert (output / "nested").is_dir()
    assert (output / "nested/file.txt").read_bytes() == data


def test_rejects_unsigned_huge_size(tmp_path):
    with pytest.raises(ArchiveValidationError):
        _package(tmp_path, [_entry("huge.bin", size=0xFFFFFFFFFFFFFFFF)])


def test_rejects_file_range_overlapping_table(tmp_path):
    with pytest.raises(ArchiveValidationError):
        _package(tmp_path, [_entry("bad.bin", size=1, offset=1)])


def test_rejects_offset_size_overflow(tmp_path):
    with pytest.raises(ArchiveValidationError):
        _package(tmp_path, [_entry("bad.bin", size=2, offset=0xFFFFFFFFFFFFFFFF)])


def test_rejects_overlapping_files(tmp_path):
    with pytest.raises(ArchiveValidationError):
        _package(
            tmp_path,
            [
                _entry("one.bin", file_hash=1, size=4, offset=0),
                _entry("two.bin", file_hash=2, size=4, offset=2),
            ],
            b"123456",
        )


def test_parser_accepts_thousands_of_reverse_ordered_ranges(tmp_path):
    count = 5000
    data = b"0" * (count * 2)
    entries = [
        _entry(
            f"file-{index}.bin",
            file_hash=index + 1,
            size=1,
            offset=(count - index - 1) * 2,
        )
        for index in range(count)
    ]

    package = _package(tmp_path, entries, data)

    assert len(package.files) == count


def test_parser_rejects_a_late_overlap_after_range_sort(tmp_path):
    count = 5000
    data = b"0" * (count * 2)
    entries = [
        _entry(
            f"file-{index}.bin",
            file_hash=index + 1,
            size=1,
            offset=index * 2,
        )
        for index in range(count)
    ]
    entries[-1] = _entry(
        f"file-{count - 1}.bin",
        file_hash=count,
        size=2,
        offset=(count - 2) * 2,
    )

    with pytest.raises(ArchiveValidationError):
        _package(tmp_path, entries, data)


def test_enforces_file_count_quota(tmp_path):
    table = _entry("one.bin", file_hash=1)
    archive = tmp_path / "content.kspkg"
    archive.write_bytes(table + bytes(256))
    package = KsPck(archive, max_file_count=0)
    package.FILE_TBL_SZ = len(table) + 256
    with pytest.raises(ArchiveValidationError):
        package.parse_file_tbl()


def test_enforces_total_output_quota(tmp_path):
    data = b"1234"
    with pytest.raises(ArchiveValidationError):
        _package_with_limits(
            tmp_path,
            [_entry("file.bin", size=len(data))],
            data,
            max_total_output_size=3,
        )


def _package_with_limits(tmp_path, entries, data, **limits):
    table = b"".join(entries) + bytes(256)
    archive = tmp_path / "content.kspkg"
    archive.write_bytes(data + table)
    package = KsPck(archive, **limits)
    package.FILE_TBL_SZ = len(table)
    package.parse_file_tbl()
    return package


class _ShortReader:
    def seek(self, *_args):
        return 0

    def read(self, _size):
        return b"shor"


def test_rejects_short_member_read(tmp_path):
    package = _package(tmp_path, [_entry("file.bin", size=5)], b"12345")
    package.kspck = _ShortReader()
    with pytest.raises(ArchiveValidationError):
        package.extract_internal(package.files[1], tmp_path / "out")
