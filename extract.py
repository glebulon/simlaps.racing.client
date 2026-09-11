import argparse
import enum
import io
import pathlib
import re
import struct
import time


class ArchiveValidationError(ValueError):
    """Raised when a package contains invalid or unsafe archive metadata."""


# Generous for ACE content, but bounded so an untrusted package cannot request
# unbounded filesystem, memory, or metadata work.
MAX_FILE_SIZE = 256 * 1024 * 1024
MAX_TOTAL_OUTPUT_SIZE = 4 * 1024 * 1024 * 1024
MAX_FILE_COUNT = 100_000
_WINDOWS_DEVICE_NAME = re.compile(r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$", re.IGNORECASE)


def _validate_non_overlapping_ranges(ranges: list[tuple[int, int]]) -> None:
    """Reject overlapping data ranges with one sort and adjacent scan."""
    ranges.sort()
    previous_end = None
    for start, end in ranges:
        if previous_end is not None and start < previous_end:
            raise ArchiveValidationError("archive members have overlapping data ranges")
        previous_end = end


#            twitter.com/@ntpopgetdope
#        github.com//ntpopgetdope/ace-kspkg
# ------------------------------------------------
# All reverse engineering peformed on the 16/01/25
# against following version of AssettoCorsaEVO.exe
# ------------------------------------------------
# Build release 0x312e30, version 250116_022721,
# revision 9468f152c075f15ff3c58a38bc724f8e4e6546a4,
# steam appid 3058630, built on Jan 16 2025, 02:28:31


class FnvHash:
    FNV_64_PRIME = 0x100000001B3
    FNV1_64A_OFF = 0xCBF29CE484222325
    # http://www.isthe.com/chongo/tech/comp/fnv/index.html#FNV-1a

    @staticmethod
    def fnv1a_64(data):
        assert isinstance(data, bytes)  # noqa: S101
        h = FnvHash.FNV1_64A_OFF
        for b in data:
            h = ((h ^ b) * FnvHash.FNV_64_PRIME) % (2**64)
        return h


# ------------------------------------------------------------------------------------------


class KsPckFile:
    FILE_PATH_SZ = 0xE0

    class FileFlags(enum.IntFlag):
        Directory = ((1 << 0),)
        XorCipher = ((1 << 8),)

    def __init__(self, raw: bytes):
        self.raw = io.BytesIO(raw)
        max_path = self.FILE_PATH_SZ

        # Unpacking of these happens @ 0x14140f50b within the main parse loop.
        try:
            self.file_path = struct.unpack(f"<{max_path}s", self.raw.read(max_path))[0]  # +0x00
            self.align_0E0 = struct.unpack("<I", self.raw.read(4))[0]  # +0xE0
            self.inf_flags = struct.unpack("<H", self.raw.read(2))[0]  # +0xE4
            self.path_leng = struct.unpack("<H", self.raw.read(2))[0]  # +0xE6
            # FNV1A-64 hash of the file path used to order file table entries
            self.path_fnv1 = struct.unpack("<Q", self.raw.read(8))[0]  # +0xE8
            self.file_size = struct.unpack("<Q", self.raw.read(8))[0]  # +0xF0
            self.file_offs = struct.unpack("<Q", self.raw.read(8))[0]  # +0xF8
        except struct.error as exc:
            raise ArchiveValidationError("truncated file-table entry") from exc

        if self.path_leng > max_path:
            raise ArchiveValidationError("file-table path length exceeds entry size")
        path_bytes = self.file_path[: self.path_leng]
        if b"\x00" in path_bytes:
            raise ArchiveValidationError("file-table path contains NUL")
        try:
            decoded_path = path_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ArchiveValidationError("file-table path is not valid UTF-8") from exc

        # Cleanup path removing trailing nulls & map bitflags to IntFlag.
        self.file_path = decoded_path
        self.inf_flags = KsPckFile.FileFlags(self.inf_flags)
        return


class KsPck:
    FILE_TBL_SZ = 2 << 24
    FILE_ITM_SZ = 1 << 8

    def __init__(
        self,
        kspck_path: str,
        *,
        max_file_size: int = MAX_FILE_SIZE,
        max_total_output_size: int = MAX_TOTAL_OUTPUT_SIZE,
        max_file_count: int = MAX_FILE_COUNT,
    ):
        print(f"Parsing input KsPkg file: '{kspck_path}'")
        if any(
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 0
            for limit in (max_file_size, max_total_output_size, max_file_count)
        ):
            raise ValueError("archive quotas must be non-negative integers")
        self.files: dict[int, KsPckFile] = {}
        self.kspck = open(kspck_path, "rb")
        self.xork = None
        self.ftbl = None
        self.archive_size = None
        self.data_end = None
        self.max_file_size = max_file_size
        self.max_total_output_size = max_total_output_size
        self.max_file_count = max_file_count
        return

    def __exit__(self):
        self.kspck.close()
        return

    @staticmethod
    def xor_8b_cipher(buffer: bytes | bytearray, xork: bytes) -> bytearray:
        # Need a mutable view of buffer.
        if isinstance(buffer, bytes):
            buffer = bytearray(buffer)

        for i, _b in enumerate(buffer):
            buffer[i] ^= xork[i % 8]

        # Return ciphertext.
        return buffer

    @staticmethod
    def _safe_member_parts(member_path: str) -> tuple[str, ...]:
        """Validate an archive path using Windows path rules.

        Both slash styles are accepted when used consistently.  Rejecting
        mixed separators prevents alternate spellings of traversal paths and
        keeps extraction deterministic across Windows and POSIX test hosts.
        """
        if not isinstance(member_path, str) or not member_path:
            raise ArchiveValidationError("archive member has an empty path")
        if "\x00" in member_path:
            raise ArchiveValidationError("archive member path contains NUL")
        if "/" in member_path and "\\" in member_path:
            raise ArchiveValidationError("archive member path mixes separators")
        if ":" in member_path:
            raise ArchiveValidationError("archive member path contains a drive or stream prefix")

        normalized = member_path.replace("\\", "/")
        if normalized.startswith("/"):
            raise ArchiveValidationError("archive member path is absolute")

        parts = tuple(normalized.split("/"))
        if any(not part for part in parts):
            raise ArchiveValidationError("archive member path contains an empty component")
        for part in parts:
            if part == "..":
                raise ArchiveValidationError("archive member path contains '..'")
            # These names are interpreted as devices by Windows, even with an
            # extension or a trailing dot/space.
            if part.endswith((".", " ")):
                raise ArchiveValidationError("archive member path has a Windows-unsafe component")
            if _WINDOWS_DEVICE_NAME.fullmatch(part.rstrip(" .")):
                raise ArchiveValidationError("archive member path uses a reserved Windows device name")
        return parts

    def _destination(self, member_path: str, out_path: str) -> pathlib.Path:
        parts = self._safe_member_parts(member_path)
        root = pathlib.Path(".") if str(out_path).casefold() == "content" else pathlib.Path(out_path)
        root = root.resolve(strict=False)
        destination = root.joinpath(*parts).resolve(strict=False)
        if destination != root and root not in destination.parents:
            raise ArchiveValidationError("archive member path escapes extraction root")
        return destination

    def _validate_file_metadata(
        self, file: KsPckFile, data_end: int, ranges: list[tuple[int, int]], total_size: int
    ) -> int:
        self._safe_member_parts(file.file_path)
        if KsPckFile.FileFlags.Directory in file.inf_flags:
            return total_size
        if (
            isinstance(file.file_size, bool)
            or not isinstance(file.file_size, int)
            or isinstance(file.file_offs, bool)
            or not isinstance(file.file_offs, int)
            or file.file_size < 0
            or file.file_offs < 0
        ):
            raise ArchiveValidationError("archive member has a negative or invalid range")
        if file.file_size > self.max_file_size:
            raise ArchiveValidationError("archive member exceeds per-file size quota")
        if file.file_size > data_end:
            raise ArchiveValidationError("archive member size exceeds data region")
        if file.file_offs > data_end or file.file_size > data_end - file.file_offs:
            raise ArchiveValidationError("archive member range exceeds data region")
        end = file.file_offs + file.file_size
        ranges.append((file.file_offs, end))
        total_size += file.file_size
        if total_size > self.max_total_output_size:
            raise ArchiveValidationError("archive exceeds total output-size quota")
        return total_size

    def parse_file_tbl(self, save_ftbl: bool = False) -> None:
        # 0x14140f2ce "ResourceManager::ParseKsPkgPackedContent"
        # 0x14140f2ce -> std::basic_istream<char,struct std::char_traits<char> >::seekg(&ios, -0x2000000, SEEK_END);
        # 0x14140f2e2 -> std::basic_istream<char,struct std::char_traits<char> >::read(&ios, pkg_file_tbl, 0x2000000);
        self.kspck.seek(0, 2)
        self.archive_size = self.kspck.tell()
        if self.archive_size < self.FILE_TBL_SZ:
            raise ArchiveValidationError("package is smaller than its file table")
        if self.FILE_TBL_SZ % self.FILE_ITM_SZ:
            raise ArchiveValidationError("file table size is not aligned to an entry")
        self.data_end = self.archive_size - self.FILE_TBL_SZ
        self.kspck.seek(-self.FILE_TBL_SZ, 2)
        self.ftbl = self.kspck.read(self.FILE_TBL_SZ)
        if len(self.ftbl) != self.FILE_TBL_SZ:
            raise ArchiveValidationError("short file-table read")

        # [NOTE] KsPkg file tables are obfuscated with stupid SIMD optimised XOR cipher (sub_14140b650)
        # obvious due to repeated 0 pad patterns :@ end of region, we can get the XOR key for free...
        # j_ResourceManager::XorBufferSimd(rsrc_mgr, pck_file_tbl, tbl_size_bytes: 0x2000000)
        # rcx: pkg_file_tbl, rdx: 0x2000000, r8: 0x9F9721A97D1135C1 (key; swap endianness)

        # XOR key usually obtained via *(rsrc_mgr + 0x758)
        self.xork = self.ftbl[-8:]  # Valid if nulls...
        ascii = "".join(f"{b:02X}" for b in self.xork)
        print(f"File Table XOR Key: {ascii}")
        print("Unciphering KsPkg file table...\n")

        # Obtain plaintext file table.
        self.ftbl = bytearray(self.ftbl)
        self.ftbl = self.xor_8b_cipher(self.ftbl, self.xork)

        if save_ftbl:  # Optionally save plaintext to disk.
            with open(f"{self.kspck.name}.unxor_file_table.bin", "wb") as f:
                # Good SHA-1: f55c845e896366014e614267ec0936ff8f237c9e
                f.write(self.ftbl)

        # Enumerate all file table entries in the KsPkg (sized @ 256 bytes each)
        ranges: list[tuple[int, int]] = []
        total_size = 0
        for i in range(0, int(self.FILE_TBL_SZ / self.FILE_ITM_SZ)):
            # Unpack file entry struct to pythonic class wrapper.
            idx = i * self.FILE_ITM_SZ  # Index in 0x100 bounds.
            file_entry = self.ftbl[idx : idx + self.FILE_ITM_SZ]
            file_entry = KsPckFile(file_entry)

            # Logic @ 0x14140f338 which breaks
            # parse loop upon NULL FNV1-A hash:
            if file_entry.path_fnv1 == 0:
                break

            if i + 1 > self.max_file_count:
                raise ArchiveValidationError("package exceeds file-count quota")
            total_size = self._validate_file_metadata(file_entry, self.data_end, ranges, total_size)

            # Store by hash for later operations/lookup.
            self.files[file_entry.path_fnv1] = file_entry

        # Check adjacent ranges after one sort, avoiding quadratic pairwise
        # comparisons for a large untrusted file table.
        _validate_non_overlapping_ranges(ranges)
        # Done.
        return

    def extract_internal(self, file: KsPckFile, out_path: str):
        path = self._destination(file.file_path, out_path)

        # If file entry is a directory, create path & exit.
        if KsPckFile.FileFlags.Directory in file.inf_flags:
            path.mkdir(parents=True, exist_ok=True)
            return

        # Read packed from relative to offset.
        if self.data_end is None:
            # Keep direct use of extract_internal safe even when callers have
            # constructed a record without first parsing the table.
            self.kspck.seek(0, 2)
            self.archive_size = self.kspck.tell()
            if self.archive_size < self.FILE_TBL_SZ:
                raise ArchiveValidationError("package is smaller than its file table")
            self.data_end = self.archive_size - self.FILE_TBL_SZ
        self._validate_file_metadata(file, self.data_end, [], 0)
        self.kspck.seek(file.file_offs, 0)
        data = self.kspck.read(file.file_size)
        if len(data) != file.file_size:
            raise ArchiveValidationError("short archive member read")

        # Handle files which use the static XOR ciphering.
        if KsPckFile.FileFlags.XorCipher in file.inf_flags:
            if not self.xork or len(self.xork) != 8:
                raise ArchiveValidationError("archive member requires a missing XOR key")
            data = self.xor_8b_cipher(data, self.xork)

        # Write processed data to unpacked file dir.
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)

    def extract_file(self, file_path: str, out_path: str) -> None:
        lookup = file_path.casefold().replace("/", "\\")
        print(f"Extracting single KsPkg file '{lookup}'...")

        fnv1a = FnvHash.fnv1a_64(lookup.encode())
        file = self.files.get(fnv1a)

        if not file:
            print(f"File lookup by FNV1A-64 0x{fnv1a:04x} failed")
            return

        # Otherwise, extract located single file.
        self.extract_internal(file, out_path)

    def extract_all(self, out_path: str) -> None:
        print(f"Extracting {len(self.files)} total KsPkg files...")
        start_time = time.perf_counter()

        # Extract all KsPkg file entries to disk...
        # Good SHA-1: 26c9b2a3517c1a1bc2da9e149499c60f34148ad1-00005ACC
        #             737b6571b6420d6a3530a5912033c109f52d94aa-0000751E
        for i, f in enumerate(self.files.values()):
            print(f"  [{i + 1:03d}/{len(self.files):03d}] 0x{f.file_size:08X} bytes: {f.file_path}")
            # Very slow... could do with multithreading and
            # handling of sync issues around file seek etc.
            # [TODO] GoLang port of this entire script lol
            self.extract_internal(f, out_path)

        # Finished extraction.
        print(f"Extracted {len(self.files)} files in {time.perf_counter() - start_time:.3f} seconds.")

    def list_all(self):
        for i, f in enumerate(self.files.values()):
            print(
                f"KsPkg File #{i}\n"
                f"  -> Path:   {f.file_path}\n"
                f"  -> Flags:  {f.inf_flags.name}\n"
                f"  -> FNV1A:  0x{f.path_fnv1:08X}\n"
                f"  -> Size:   0x{f.file_size:08x}\n"
                f"  -> Offset: 0x{f.file_offs:08x}\n"
            )

    def run_unpacked(self):
        try:
            # N.B. must close internal file
            # handle before renaming it....
            if self.kspck:
                self.kspck.close()

            print("Forcing AC:Evo to use unpacked content...")
            # AC:Evo will run from unpacked resources if the
            # content.kspkg file cannot be resolved in curdir.
            ace_kspkg = pathlib.Path(self.kspck.name).resolve()
            if ace_kspkg.exists() and ace_kspkg.is_file():
                ace_kspkg.rename(f"{ace_kspkg}.bkup")

        except PermissionError as e:
            print(f"Unable to rename KsPkg, exception: {e}")


# ------------------------------------------------------------------------------------------


def init_argparse() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Assetto Corsa Evo: Kunos Package (kspkg) Extraction Tool",
        epilog="https://github.com/ntpopgetdope/ace-kspkg",
    )

    parser.add_argument("-l", "--list", action="store_true", help="List all files packed within parsed KsPkg")
    parser.add_argument(
        "-i",
        "--in",
        type=str,
        default="content.kspkg",
        metavar="PATH",
        help="Path to KsPkg file (default: content.kspkg)",
    )

    ex = parser.add_argument_group("extract")
    # Exclusive options for all/single file extraction...
    exopt = ex.add_mutually_exclusive_group()
    exopt.add_argument("-a", "--all", action="store_true", help="Extract all files within parsed KsPkg")
    exopt.add_argument("-p", "--path", type=str, metavar="PATH", help="Extract a single file by path in KsPkg")

    # Non-exclusive extraction options...
    ex.add_argument(
        "-o", "--out", type=str, default="content", metavar="PATH", help="Path to extract KsPkg to (default: content)"
    )
    ex.add_argument("-r", "--run-unpacked", action="store_true", help="Force AC:Evo to run the unpacked content")

    # Return file parser.
    return parser


# ------------------------------------------------------------------------------------------

if __name__ == "__main__":
    print(
        "Assetto Corsa Evo: Kunos Package (kspkg) Extraction Tool\n"
        "           github.com//ntpopgetdope/ace-kspkg\n"
        "               twitter.com/@ntpopgetdope\n"
    )
    parser = init_argparse()
    args = parser.parse_args()

    # Require at least one option specified (unpack overrides behaviour)
    if not any([args.list, args.all, args.path, args.run_unpacked]):
        parser.print_help()
        exit(0)

    pck = KsPck(getattr(args, "in") or "content.kspkg")
    pck.parse_file_tbl()

    if args.list:
        pck.list_all()

    if args.all or args.run_unpacked:
        pck.extract_all(args.out)
    elif args.path:  # Single file extraction.
        pck.extract_file(args.path, args.out)

    if args.run_unpacked:
        pck.run_unpacked()
