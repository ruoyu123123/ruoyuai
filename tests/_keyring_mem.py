"""测试用内存 keyring 后端——经 keyring.set_keyring(MemKeyring()) 注入，
绝不碰真 Windows Credential Manager。测试 try/finally 还原原后端防跨测试污染。"""
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError


class MemKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()          # set_keyring 校验须是 KeyringBackend 实例
        self._s = {}

    def get_password(self, service, username):
        return self._s.get((service, username))

    def set_password(self, service, username, password):
        self._s[(service, username)] = password

    def delete_password(self, service, username):
        if (service, username) not in self._s:
            raise PasswordDeleteError("not found")
        del self._s[(service, username)]
