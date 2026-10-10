import os
import sys

if sys.version_info >= (3, 12):
    os_path_splitroot = os.path.splitroot

elif os.name ==  'posix':
    # from Lib/posixpath.py
    def os_path_splitroot(p):
        """Split a pathname into drive, root and tail.

        The tail contains anything after the root."""
        p = os.fspath(p)
        if isinstance(p, bytes):
            sep = b'/'
            empty = b''
        else:
            sep = '/'
            empty = ''
        if p[:1] != sep:
            # Relative path, e.g.: 'foo'
            return empty, empty, p
        elif p[1:2] != sep or p[2:3] == sep:
            # Absolute path, e.g.: '/foo', '///foo', '////foo', etc.
            return empty, sep, p[1:]
        else:
            # Precisely two leading slashes, e.g.: '//foo'. Implementation defined per POSIX, see
            # https://pubs.opengroup.org/onlinepubs/9699919799/basedefs/V1_chap04.html#tag_04_13
            return empty, p[:2], p[2:]

elif os.name == 'nt':
    # from Lib/ntpath.py
    def os_path_splitroot(p):
        """Split a pathname into drive, root and tail.

        The tail contains anything after the root."""
        p = os.fspath(p)
        if isinstance(p, bytes):
            sep = b'\\'
            altsep = b'/'
            colon = b':'
            unc_prefix = b'\\\\?\\UNC\\'
            empty = b''
        else:
            sep = '\\'
            altsep = '/'
            colon = ':'
            unc_prefix = '\\\\?\\UNC\\'
            empty = ''
        normp = p.replace(altsep, sep)
        if normp[:1] == sep:
            if normp[1:2] == sep:
                # UNC drives, e.g. \\server\share or \\?\UNC\server\share
                # Device drives, e.g. \\.\device or \\?\device
                start = 8 if normp[:8].upper() == unc_prefix else 2
                index = normp.find(sep, start)
                if index == -1:
                    return p, empty, empty
                index2 = normp.find(sep, index + 1)
                if index2 == -1:
                    return p, empty, empty
                return p[:index2], p[index2:index2 + 1], p[index2 + 1:]
            else:
                # Relative path with root, e.g. \Windows
                return empty, p[:1], p[1:]
        elif normp[1:2] == colon:
            if normp[2:3] == sep:
                # Absolute drive-letter path, e.g. X:\Windows
                return p[:2], p[2:3], p[3:]
            else:
                # Relative path with drive, e.g. X:Windows
                return p[:2], empty, p[2:]
        else:
            # Relative path, e.g. Windows
            return empty, empty, p

else:
    raise ImportError('no os specific module found')
