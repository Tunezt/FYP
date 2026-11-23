# Kuesioner SUS / System Usability Scale

For the trial with the owner and Ibu Diah (CP1 §3.8). Print it, or read each line out and
write the number down. Scoring: `python scripts/sus_score.py answers.csv` (target 72 or more).
Answer for the system as a whole: the WhatsApp assistant, the till and the web dashboard.

**Skala / Scale:** 1 = sangat tidak setuju / strongly disagree, 2 = tidak setuju / disagree,
3 = ragu-ragu / neutral, 4 = setuju / agree, 5 = sangat setuju / strongly agree.

| # | Indonesia | English | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|---|---|
| 1 | Saya rasa saya ingin memakai sistem ini sesering mungkin. | I think that I would like to use this system frequently. | ☐ | ☐ | ☐ | ☐ | ☐ |
| 2 | Saya merasa sistem ini terlalu rumit padahal tidak perlu. | I found the system unnecessarily complex. | ☐ | ☐ | ☐ | ☐ | ☐ |
| 3 | Saya merasa sistem ini mudah dipakai. | I thought the system was easy to use. | ☐ | ☐ | ☐ | ☐ | ☐ |
| 4 | Saya rasa saya akan butuh bantuan orang teknis untuk bisa memakai sistem ini. | I think that I would need the support of a technical person to be able to use this system. | ☐ | ☐ | ☐ | ☐ | ☐ |
| 5 | Saya merasa fungsi-fungsi di sistem ini tersusun dengan baik. | I found the various functions in this system were well integrated. | ☐ | ☐ | ☐ | ☐ | ☐ |
| 6 | Saya merasa ada terlalu banyak hal yang tidak konsisten di sistem ini. | I thought there was too much inconsistency in this system. | ☐ | ☐ | ☐ | ☐ | ☐ |
| 7 | Saya membayangkan kebanyakan orang akan cepat belajar memakai sistem ini. | I would imagine that most people would learn to use this system very quickly. | ☐ | ☐ | ☐ | ☐ | ☐ |
| 8 | Saya merasa sistem ini sangat merepotkan dipakai. | I found the system very cumbersome to use. | ☐ | ☐ | ☐ | ☐ | ☐ |
| 9 | Saya merasa sangat yakin saat memakai sistem ini. | I felt very confident using the system. | ☐ | ☐ | ☐ | ☐ | ☐ |
| 10 | Saya perlu belajar banyak hal dulu sebelum bisa memakai sistem ini. | I needed to learn a lot of things before I could get going with this system. | ☐ | ☐ | ☐ | ☐ | ☐ |

Nama / Name: ______________________  Peran / Role (pemilik, kasir): ______________  Tanggal / Date: ____________

## Answer sheet for the scoring script

`answers.csv`, one line per person (the number ticked for items 1 to 10, in order):

```
name,q1,q2,q3,q4,q5,q6,q7,q8,q9,q10
Ibu Diah,4,2,4,2,4,2,4,2,4,2
```

Items 1, 3, 5, 7, 9 are positive and 2, 4, 6, 8, 10 are negative; the script handles that.
The example line above is a made-up sample for the format, not a result.

## Notes for whoever runs it

- Wording follows the standard 10 SUS items; the Indonesian is a plain-language translation,
  worth a read-through by Ibu Diah before use, because a stiff word changes the answer.
- With two or three participants the mean is an indication, not a statistic. Report n.
