Logo templates for `addons/news/clean_footage.py`: steady-edge crops (not pictures) saved with
`clean_footage.py projects/<slug> --save-template <name> <file> x y w h`. A name with `text` in it is matched more
strictly (lettering resembles any lettering).

| Template | Where it comes from |
|---|---|
| `gibdd_kazan_emblem` | Госавтоинспекция Казани camera videos, emblem top right (`news-kzn-vypusk-28-10`, naz_kc2) |
| `gibdd_kazan_badge` | Госавтоинспекция Казани blue badge «Сберегите себя! Сохраните других!» on the left (naz_kc1) |
| `region116_logo` | Region 116 round logo (Kazan footage via tatarstansos, brawl_tt) |
| `region116_text` | «Телеграм канал Region116 / t.me/region116_kazan» next to it |
| `fontanka_text` | «фонтанка.ру» with its round emblem across the middle of the frame (SPb, theatre.mp4 28.10): the slow camera kept the buildings steady too, so this template keeps only edges that point the same way in every frame (made by hand, not with --save-template) |
| `tatarinform_logo` | «ТАТАР ИНФОРМ» wordmark bottom right of tatarstansos photos (`news-kzn-vypusk-30-10`, m7_car.jpg, saved from a photo) |
