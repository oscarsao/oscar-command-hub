' Resumen diario de Hermes (t_631cf0bc). Generado por instalar-brief.ps1: no editar a mano.
' python.exe con la ventana oculta (0) y esperando a que termine (True). Errores en tools\agent-lanes\.state\brief.log
Option Explicit
Dim sh, env
Set sh = CreateObject("WScript.Shell")
Set env = sh.Environment("PROCESS")
env.Item("PYTHONIOENCODING") = "utf-8"
sh.CurrentDirectory = "C:\Users\oscar\oscar-command-hub\tools\agent-lanes"
sh.Run """C:\Users\oscar\AppData\Local\Programs\Python\Python312\python.exe"" ""C:\Users\oscar\oscar-command-hub\tools\agent-lanes\brief\brief_diario.py""", 0, True
