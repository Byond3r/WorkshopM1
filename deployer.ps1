# Copie les fichiers de pi/ sur le Raspberry et redémarre les services.
# À lancer depuis la racine du projet, PC connecté au Wi-Fi Ultron :
#   powershell -ExecutionPolicy Bypass -File deployer.ps1

$PI = "user@10.42.0.1"
$DOSSIER_PI = "/home/user/sentinel/"
$FICHIERS = @(
    "pi/serveur.py", "pi/dashboard.html", "pi/capture_reseau.py", "pi/capteurs_pi.py",
    "pi/sentinel.service", "pi/sentinel-reseau.service", "pi/sentinel-capteurs.service", "pi/installer.sh", "pi/durcir.sh",
    "pi/generer_certificat.sh"
)

Write-Host "Copie des fichiers vers $PI ..."
scp $FICHIERS "${PI}:${DOSSIER_PI}"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Copie impossible : le PC est-il bien connecte a Ultron ? (ping 10.42.0.1)"
    exit 1
}

Write-Host "Redemarrage des services ..."
# sentinel-reseau et sentinel-capteurs ne sont redemarres que s'ils sont installes (sinon on les ignore)
# -t : ouvre un vrai terminal, pour que sudo puisse demander le mot de passe
# `$s : l'accent grave empeche PowerShell de remplacer $s ; c'est bash, sur le Pi, qui doit le lire
ssh -t $PI "sudo systemctl restart sentinel; for s in sentinel-reseau sentinel-capteurs; do systemctl is-enabled --quiet `$s && sudo systemctl restart `$s; done; systemctl is-active sentinel sentinel-reseau sentinel-capteurs"
