"""Private Linux regular-file credentials; never falls back to env or interactive input."""
import os,stat
from pathlib import Path
class PrivateSecretError(Exception):pass

def secret_metadata(path):
 try:
  p=Path(path);s=p.lstat()
  valid=(os.name=='posix' and stat.S_ISREG(s.st_mode) and not p.is_symlink() and s.st_nlink==1 and s.st_uid==os.geteuid() and stat.S_IMODE(s.st_mode) in (0o400,0o600) and 20<=s.st_size<=512)
  return {'ready':valid,'status':'private-file-ready' if valid else 'unsafe-file-metadata'}
 except OSError:return {'ready':False,'status':'credential-file-unavailable'}

def read_secret(path):
 if not secret_metadata(path)['ready']:raise PrivateSecretError('credential-file-unavailable-or-unsafe')
 fd=None
 try:
  fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW);s=os.fstat(fd)
  if not stat.S_ISREG(s.st_mode) or s.st_nlink!=1 or s.st_uid!=os.geteuid() or stat.S_IMODE(s.st_mode) not in (0o400,0o600):raise PrivateSecretError('credential-file-changed')
  raw=os.read(fd,513)
  if not 20<=len(raw)<=512:raise PrivateSecretError('credential-file-size')
  value=raw.decode('ascii').strip()
  if not value or any(c.isspace() for c in value):raise PrivateSecretError('credential-file-format')
  return value
 except (OSError,UnicodeError):raise PrivateSecretError('credential-file-unavailable-or-unsafe') from None
 finally:
  if fd is not None:os.close(fd)
