# This file is part of Xpra.
# Copyright (C) 2013-2020 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os

from xpra.server.proxy.proxy_server import ProxyServer as _ProxyServer
from xpra.platform.paths import get_app_dir
from xpra.util import envbool
from xpra.os_util import pollwait, strtobytes
from xpra.log import Logger

log = Logger("proxy")


def exec_command(username, args, exe, cwd, env):
    log("exec_command%s", (username, args, exe, cwd, env))
    from xpra.platform.win32.lsa_logon_lib import logon_msv1_s4u
    logon_info = logon_msv1_s4u(username)
    log("logon_msv1_s4u(%s)=%s", username, logon_info)
    from xpra.platform.win32.create_process_lib import (
        Popen,
        CREATIONINFO, CREATION_TYPE_TOKEN,
        LOGON_WITH_PROFILE, CREATE_NEW_PROCESS_GROUP, STARTUPINFO,
        )
    creation_info = CREATIONINFO()
    creation_info.dwCreationType = CREATION_TYPE_TOKEN
    creation_info.dwLogonFlags = LOGON_WITH_PROFILE
    creation_info.dwCreationFlags = CREATE_NEW_PROCESS_GROUP
    creation_info.hToken = logon_info.Token
    log("creation_info=%s", creation_info)
    startupinfo = STARTUPINFO()
    startupinfo.lpDesktop = "WinSta0\\Default"
    startupinfo.lpTitle = "Xpra-Shadow"
    from subprocess import PIPE
    proc = Popen(args, executable=exe,
                 stdout=PIPE, stderr=PIPE,
                 cwd=cwd, env=env,
                 startupinfo=startupinfo, creationinfo=creation_info)
    log("Popen(%s)=%s", args, proc)
    return proc


class ProxyServer(_ProxyServer):

    def start_new_session(self, username, password, uid, gid, new_session_dict=None, displays=()):
        log("start_new_session%s", (username, "..", uid, gid, new_session_dict, displays))
        return self.start_win32_shadow(username, password, new_session_dict)

    def start_win32_shadow(self, username, password, new_session_dict):
        log("start_win32_shadow%s", (username, "..", new_session_dict))
        #first, Logon:
        try:
            from xpra.platform.win32.desktoplogon_lib import Logon
            Logon(strtobytes(username), strtobytes(password))
        except Exception:
            log.error("Error: failed to logon as '%s'", username, exc_info=True)
        #hwinstaold = set_window_station("winsta0")
        #whoami = os.path.join(get_app_dir(), "whoami.exe")
        #exec_command([whoami])
        app_dir = get_app_dir()
        shadow_command = os.path.join(app_dir, "Xpra-Shadow.exe")
        paexec = os.path.join(app_dir, "paexec.exe")
        named_pipe = username.replace(" ", "_")
        cmd = []
        exe = shadow_command

        #use paexec to access the GUI session:
        if envbool("XPRA_PAEXEC", True) and os.path.exists(paexec) and os.path.isfile(paexec):
            #find the session-id to shadow:
            from xpra.platform.win32.wtsapi import find_session
            info = find_session(username)
            if info:
                cmd = [
                    "paexec.exe",
                    "-i", str(info["SessionID"]), "-s",
                    ]
                exe = paexec
            else:
                log.warn("Warning: session not found for username '%s'", username)
        else:
            log.warn("Warning: starting without paexec, expect a black screen")
        
        cmd += [
            shadow_command,
            "--bind=%s" % named_pipe,
            #"--tray=no",
            ]
        from xpra.log import debug_enabled_categories
        if debug_enabled_categories:
            cmd += ["-d", ",".join(tuple(debug_enabled_categories))]
        #command += ["-d", "all"]
        env = self.get_proxy_env()
        #env["XPRA_ALL_DEBUG"] = "1"
        #env["XPRA_REDIRECT_OUTPUT"] = "1"
        #env["XPRA_LOG_FILENAME"] = "E:\\Shadow-Instance.log"
        proc = exec_command(username, cmd, exe, app_dir, env)
        #TODO: poll the named pipe instead of waiting
        r = pollwait(proc, 4)
        if r is not None:
            log("pollwait=%s", r)
            try:
                log("stdout=%s", proc.stdout.read())
                log("stderr=%s", proc.stderr.read())
            except (OSError, AttributeError):
                log("failed to read stdout / stderr of subprocess", exc_info=True)
            if r!=0:
                raise Exception("shadow subprocess failed with exit code %s" % r)
            else:
                raise Exception("shadow subprocess has already terminated")
        self.child_reaper.add_process(proc, "server-%s" % username, "xpra shadow", True, True)
        return proc, "named-pipe://%s" % named_pipe, named_pipe
