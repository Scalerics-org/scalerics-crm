' Arranca el lector de WhatsApp de la Oficina sin ventana. Lo llama el acceso
' de Inicio de Windows que crea "Conectar WhatsApp a la Oficina.bat".
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
carpeta = fso.GetParentFolderName(WScript.ScriptFullName)
datos = sh.ExpandEnvironmentStrings("%APPDATA%") & "\Scalerics\wa-lector"
If Not fso.FolderExists(datos) Then fso.CreateFolder(datos)
sh.CurrentDirectory = carpeta
sh.Run "cmd /c node lector.js >> """ & datos & "\lector.log"" 2>&1", 0, False
