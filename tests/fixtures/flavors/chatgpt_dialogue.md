Here is the comparison you asked for.

## A. Component identification

| Network | Part                  | Location          |
| ------- | --------------------- | ----------------- |
| `NET_A` | \\(R\_{\text{A1}}\\)  | Group one, left   |
| `NET_A` | \\(R\_{\text{A2}}\\)  | Group one, right  |
| `NET_B` | \\(C\_{\text{B1}}\\)  | Group two, centre |

One caveat: the pin map names the networks, but the divider ends still need checking.

## B. The threshold

The datasheet gives:

\\[ V\_{\text{fall}} = 2.5\left(1+\frac{R\_{\text{A2}}}{R\_{\text{A1}}}\right) \\]

\\[ V\_{\text{rise}} = 2.5\left(1+\frac{R\_{\text{A2}}+R\_{\text{B1}}} {R\_{\text{A1}}}\right) \\]

The first is the falling threshold; the second is the rising one, with a sum of about 13 kOhm.

[image](https://www.google.com/s2/favicons?domain=https://example.org\&sz=32)

example.org+1



I can identify the parts, but I cannot compute the exact values from in-circuit readings.

## C. Reading list

See [the vendor note](https://example.com/notes?utm_source=chatgpt.com&x=1) for details, and https://example.com/blog?utm_medium=x&utm_campaign=y for background.

A normal fence stays as it is:

```sh
echo "\(not math\)"
```
