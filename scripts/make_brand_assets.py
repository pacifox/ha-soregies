"""Génère les visuels de marque de l'intégration.

Le dessin est **volontairement neutre** : un compteur stylisé, pas une reprise
du logo Sorégies. Ce projet n'est pas officiel, et reproduire la marque du
fournisseur laisserait croire le contraire — en plus d'utiliser un signe
distinctif qui ne nous appartient pas.

Le script est versionné pour que les visuels restent reproductibles : on
regénère plutôt que de retoucher un binaire dont personne ne sait plus l'origine.

    python3 scripts/make_brand_assets.py
"""

from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw

OUT = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "soregies" / "brand"

# Bleu profond neutre, choisi pour rester lisible sur fond clair comme sombre.
INK = (14, 90, 138, 255)
GLOW = (255, 255, 255, 255)


def draw(size: int) -> Image.Image:
    """Dessine le repère à la taille demandée.

    Tout est exprimé en fraction de `size` : le rendu reste net à 256 comme à
    512 px, sans redimensionnement intermédiaire qui flouterait les bords.
    """
    scale = 4  # suréchantillonnage, puis réduction : bords lisses sans filtre
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Cadran du compteur : anneau épais plutôt qu'un disque plein, pour rester
    # lisible en 16 px dans un onglet de navigateur.
    pad = s * 0.06
    ring = s * 0.11
    d.ellipse([pad, pad, s - pad, s - pad], fill=INK)
    d.ellipse([pad + ring, pad + ring, s - pad - ring, s - pad - ring], fill=(0, 0, 0, 0))

    # Éclair inscrit dans le cadran, en pleine hauteur utile.
    cx = s / 2
    bolt = [
        (cx + s * 0.10, s * 0.20),
        (cx - s * 0.16, s * 0.52),
        (cx - s * 0.01, s * 0.52),
        (cx - s * 0.10, s * 0.80),
        (cx + s * 0.17, s * 0.45),
        (cx + s * 0.02, s * 0.45),
    ]
    # Le halo blanc détache l'éclair de l'anneau quand les deux se croisent.
    d.polygon(bolt, fill=INK)
    d.line([*bolt, bolt[0]], fill=GLOW, width=int(s * 0.022), joint="curve")

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    icon = draw(256)
    icon.save(OUT / "icon.png")
    draw(512).save(OUT / "icon@2x.png")
    # Home Assistant attend un logo de 128 px de haut ; ici il est carré, donc
    # identique à l'icône à l'échelle près.
    icon.resize((128, 128), Image.LANCZOS).save(OUT / "logo.png")
    icon.save(OUT / "logo@2x.png")
    for f in sorted(OUT.iterdir()):
        with Image.open(f) as im:
            print(f"  {f.name}: {im.size[0]}x{im.size[1]} {im.mode}")


if __name__ == "__main__":
    main()
