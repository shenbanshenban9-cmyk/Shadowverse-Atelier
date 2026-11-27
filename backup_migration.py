"""Verified backup-folder migration without overwriting existing originals."""
from pathlib import Path
import hashlib
import os
import shutil
import uuid

def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            value.update(block)
    return value.digest()

class BackupMigration:
    def __init__(self,source,target):
        self.source=Path(source).resolve()
        self.target=Path(target).resolve()
        self.files=[]
        self.created=[]
        if self.source==self.target:
            return
        if self.target.is_relative_to(self.source) or self.source.is_relative_to(self.target):
            raise ValueError('新旧备份目录不能相互包含，请选择独立目录')
        if not self.source.exists():
            return
        for file in self.source.rglob('*'):
            if file.is_symlink() or file.is_junction():
                raise ValueError('备份目录中存在链接，请选择普通文件夹')
            if not file.is_file():continue
            destination=self.target/file.relative_to(self.source)
            if not destination.resolve().is_relative_to(self.target):
                raise ValueError('目标备份目录包含外部链接，请选择普通文件夹')
            fingerprint=digest(file)
            if destination.exists() and (not destination.is_file() or digest(destination)!=fingerprint):
                raise ValueError('目标目录存在不同的同名备份：'+str(destination))
            self.files.append((file,destination,fingerprint))
        try:
            for source,destination,fingerprint in self.files:
                destination.parent.mkdir(parents=True,exist_ok=True)
                if destination.exists():continue
                temporary=destination.with_name(destination.name+'.'+uuid.uuid4().hex+'.tmp')
                try:
                    shutil.copy2(source,temporary)
                    if digest(temporary)!=fingerprint:
                        raise ValueError('备份复制校验失败，请重试')
                    # On Windows rename refuses to overwrite an existing destination.
                    os.rename(temporary,destination)
                    self.created.append(destination)
                finally:
                    temporary.unlink(missing_ok=True)
            for source,destination,fingerprint in self.files:
                if digest(source)!=fingerprint or digest(destination)!=fingerprint:
                    raise ValueError('迁移期间备份发生变化，请重试')
        except Exception:
            self.rollback()
            raise

    def rollback(self):
        for file in reversed(self.created):
            file.unlink(missing_ok=True)

    def finish(self):
        # Called after the new settings have been committed.
        for source,destination,fingerprint in self.files:
            try:
                if digest(source)==fingerprint and digest(destination)==fingerprint:
                    source.unlink()
            except OSError:
                pass
        if self.source!=self.target and self.source.exists():
            for folder in sorted((f for f in self.source.rglob('*') if f.is_dir()),key=lambda f:len(f.parts),reverse=True):
                try:folder.rmdir()
                except OSError:pass
            try:self.source.rmdir()
            except OSError:pass
