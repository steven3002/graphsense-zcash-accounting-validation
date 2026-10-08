# The dataset

Dataset version 1.0.0 (2026-10-06): 10 Zcash mainnet blocks, every transaction of each block (47 cases), and the 43 earlier transactions whose transparent outputs they spend.

This page is built from `manifest/manifest.json`, which is the authority. All amounts are in zatoshi.

## Shapes

Every case is listed under the shapes it has. A shape is a rule on values that follow from the node's fields; the
tool derives the shapes again from the evidence on every run and fails if a case's list differs.

Notation: *balance* is the signed balance of a pool (positive: value left the pool). *Net transparent value* is
the transparent input total minus the transparent output total.

| Shape | Rule |
|---|---|
| `shielding:<pool>` | Not a coinbase; the pool's balance is negative; net transparent value is positive. Transparent value entered the pool. |
| `unshielding:<pool>` | Not a coinbase; the pool's balance is positive; net transparent value is negative. Pool value reached transparent outputs. |
| `fully-shielded-fee` | Not a coinbase; no transparent input and no transparent output; the fee is greater than zero. The fee was paid from shielded value. |
| `fully-shielded-fee:<pool>` | As above, and this is the only pool with a non-zero balance: its positive balance is the fee and nothing else. |
| `sapling+orchard` | The Sapling balance and the Orchard balance are both non-zero. |
| `ironwood` | The Ironwood balance is non-zero. |
| `three-pool` | At least three of the four pools have a non-zero balance. |
| `cross-pool` | One pool has a positive balance and another a negative one: value moved between pools. |
| `sprout` | The Sprout balance is non-zero. |
| `transparent-only` | Not a coinbase; all four balances are zero. |
| `coinbase` | The transaction has a coinbase input. |
| `shielded-coinbase` | A coinbase with a negative pool balance: part of the reward went into a pool. |

A positive balance alone is never called an unshielding. `2501409/1`, `3438010/2` and `3479000/1` have a positive
balance and no transparent output: the balance is the fee. `1687194/4`, `3479000/3` and `3463373/2` have positive
balances and no transparent output: the value moved to another pool.

### Where each required shape occurs

| Shape | Cases |
|---|---|
| `shielding:sapling` | `3442130/1`, `3463373/3` |
| `shielding:orchard` | `2501409/3` |
| `shielding:ironwood` | `3442400/2`, `3463373/6`, `3479000/4` |
| `unshielding:sapling` | `3438010/1` |
| `unshielding:orchard` | `1687143/1` |
| `unshielding:ironwood` | `3438010/3`, `3463373/9`, `3479000/2` |
| `fully-shielded-fee` | `600000/5`, `600000/6`, `600000/7`, `600000/8`, `600000/9`, `600000/10` and 7 more |
| `fully-shielded-fee:sapling` | `3438010/2` |
| `fully-shielded-fee:orchard` | `2501409/1` |
| `fully-shielded-fee:ironwood` | `3479000/1` |
| `sapling+orchard` | `1687194/4`, `3463373/2` |
| `ironwood` | `3438010/3`, `3442400/2`, `3463373/2`, `3463373/6`, `3463373/9`, `3479000/1` and 3 more |
| `three-pool` | `3463373/2` |
| `cross-pool` | `600000/5`, `600000/6`, `600000/7`, `600000/8`, `600000/9`, `600000/10` and 4 more |
| `transparent-only` | `600000/1`, `600000/2`, `600000/3`, `600000/4`, `1687194/1`, `1687194/2` and 7 more |
| `sprout` | `600000/5`, `600000/6`, `600000/7`, `600000/8`, `600000/9`, `600000/10` and 1 more |
| `coinbase` | `600000/0`, `1687119/0`, `1687143/0`, `1687194/0`, `2501409/0`, `3438010/0` and 4 more |
| `shielded-coinbase` | `1687119/0` |

## Blocks

| Height | Block hash | Transactions | Previous transactions | Fees | Captures |
|---:|---|---:|---:|---:|---|
| 600000 | `00000000011502273e3726d1a229b69ae5088eeac650d787dcd5eabe1429ea38` | 11 | 5 | 73,780 | graphsense-lib-fixtures, zebra-6.4.2 |
| 1687119 | `0000000001991db51d39d17fd626a07f2f0b44aaf5c0e831519ebe457eeb671b` | 1 | 0 | 0 | zebra-6.4.2 |
| 1687143 | `00000000020252abdf313f51b343931cabfa8c07a48ca0effdb450158b1949c2` | 2 | 0 | 1,000 | zebra-6.4.2 |
| 1687194 | `00000000005cdacbf9670c4aef2b7a7cf0db62fd74eab9ff320e6b1c8c66c8bf` | 5 | 7 | 15,583 | graphsense-lib-fixtures, zebra-6.4.2 |
| 2501409 | `00000000018c04af07a9d5472e4dcb538fd1608a7c36db9c2ff2686acd2d18ee` | 4 | 4 | 12,000 | graphsense-lib-fixtures, zebra-6.4.2 |
| 3438010 | `000000000029b042d36389040f449455eddd4831ae9b058849cce4a7c3612481` | 4 | 0 | 70,000 | zebra-6.4.2 |
| 3442130 | `0000000000606a295babe75433374efde94a7e989ef58087dc13dbef72d5e7e2` | 2 | 2 | 15,000 | graphsense-lib-fixtures, zebra-6.4.2 |
| 3442400 | `00000000000e7e765c75d84472c63e9ad87b02ce6dc361c0869a4099b4608936` | 3 | 1 | 55,000 | graphsense-lib-fixtures, zebra-6.4.2 |
| 3463373 | `000000000056df75a4dbfca5164574703573f12eb18411b647589909c081bd0f` | 10 | 22 | 246,000 | graphsense-lib-fixtures, zebra-6.4.2 |
| 3479000 | `0000000000461f792f5f915c6008d4c6e97b87ae6dda49c0edd8f96a0dc38098` | 5 | 2 | 90,000 | graphsense-lib-fixtures, zebra-6.4.2 |

## Cases where the old release is wrong (13)

These transactions have a non-zero Orchard or Ironwood balance. graphsense-lib 2.16.3 never reads those balances,
so its totals and fee are off by exactly that value. "Old" is what 2.16.3 reports; a negative fee is what it reports.

| Case | Transaction | Version | Shapes | Orchard | Ironwood | Fee | Old fee | Total input | Old | Total output | Old |
|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1687143/1 | `8454f559f0c9475ae3c6746dd17e467709fba3cec84ed3dc16b911355a47b5ae` | 5 | unshielding:orchard | 10,001,000 | 0 | 1,000 | -10,000,000 | 10,001,000 | 0 | 10,000,000 | 10,000,000 |
| 1687194/4 | `bd84ece1a1f9930085768a880d99445ee1ee13686c2e5979b30d920e90866517` | 5 | cross-pool, fully-shielded-fee, sapling+orchard | -88,000 | 0 | 1,000 | 89,000 | 89,000 | 89,000 | 88,000 | 0 |
| 2501409/1 | `224daf9f01485fceacfe1fa49daf4d498c23a5c58ff237d28aa1495cd8bc7c5a` | 5 | fully-shielded-fee, fully-shielded-fee:orchard | 1,000 | 0 | 1,000 | 0 | 1,000 | 0 | 0 | 0 |
| 2501409/3 | `7bd7717f8897e323840ac0f6b09518e580dbca1fa77dbd20ed0b90fc56d2fe69` | 5 | shielding:orchard | -362,999,000 | 0 | 1,000 | 363,000,000 | 363,000,000 | 363,000,000 | 362,999,000 | 0 |
| 3438010/3 | `13ceb5a350d02ba6b9b8508e748187c454b813bef49a565a425b2951ed5f7691` | 6 | ironwood, unshielding:ironwood | 0 | 10,409,748 | 20,000 | -10,389,748 | 10,409,748 | 0 | 10,389,748 | 10,389,748 |
| 3442400/2 | `b1a00ede11a2dabd31d9465d969454f152980bc85aa92bdbf5425640a39cb857` | 6 | ironwood, shielding:ironwood | 0 | -135,028 | 15,000 | 150,028 | 4,260,000 | 4,260,000 | 4,245,000 | 4,109,972 |
| 3463373/2 | `6f37465ac2a718135c61fb62049e6205977da2042774002d301d018a76da04c8` | 6 | cross-pool, fully-shielded-fee, ironwood, sapling+orchard, three-pool | 24,898 | 505,102 | 30,000 | -500,000 | 530,000 | 0 | 500,000 | 500,000 |
| 3463373/6 | `83528670357dab26b94211f39d5dcf2e7efa776d7ca68c8762e49676e0562780` | 6 | ironwood, shielding:ironwood | 0 | -124,985,000 | 15,000 | 125,000,000 | 125,000,000 | 125,000,000 | 124,985,000 | 0 |
| 3463373/9 | `c5b7ed56b5340a88645a97a683dae5b51c62b2405582fe1e4743da85e9a274b7` | 6 | ironwood, unshielding:ironwood | 0 | 10,515,000 | 15,000 | -10,500,000 | 10,515,000 | 0 | 10,500,000 | 10,500,000 |
| 3479000/1 | `af9eae20963e5e53e2bda3275ffcd1d19decad6530ce46f55b83585336819b82` | 6 | fully-shielded-fee, fully-shielded-fee:ironwood, ironwood | 0 | 40,000 | 40,000 | 0 | 40,000 | 0 | 0 | 0 |
| 3479000/2 | `aee031d4299acacdfc4d1b0bfc6d34d760f5b759e187a202a917f3b297af60a2` | 6 | ironwood, unshielding:ironwood | 0 | 300,015,000 | 15,000 | -300,000,000 | 300,015,000 | 0 | 300,000,000 | 300,000,000 |
| 3479000/3 | `3115796d7ebd1f35d270229221166f2318888587153a294dfb712ef620899e53` | 6 | cross-pool, fully-shielded-fee, ironwood | 1,020,000 | -1,000,000 | 20,000 | 0 | 1,020,000 | 0 | 1,000,000 | 0 |
| 3479000/4 | `e6b8618cfeccce30cec8ad811768231f82bb1f93b8cec4d789537cdf2bde8f1b` | 6 | ironwood, shielding:ironwood | 0 | -2,132,669 | 15,000 | 2,147,669 | 661,586,470 | 661,586,470 | 661,571,470 | 659,438,801 |

## All cases (47)

"Old release" says whether graphsense-lib 2.16.3 is expected to report the same values as the expected ones.

| Case | Transaction | Version | Shapes | Transparent in | Transparent out | Sprout | Sapling | Orchard | Ironwood | Total input | Total output | Fee | Old release |
|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 600000/0 | `99c678be78dcceaad8f8719917a315614610886f2036dbb4bc5c6ace129f0a28` | 4 | coinbase | 0 | 1,250,073,780 | 0 | 0 | 0 | 0 | 0 | 1,250,073,780 | 0 | same |
| 600000/1 | `8b231f950ee915d65baf74eef6eb969e3cb031d50b6477cf66440004af004011` | 4 | transparent-only | 1,097,948,897,251 | 1,097,948,894,611 | 0 | 0 | 0 | 0 | 1,097,948,897,251 | 1,097,948,894,611 | 2,640 | same |
| 600000/2 | `a9444d3678eb6987f0044b94302a21e9a67074a027182b72a99bf21ae1a6d0d5` | 4 | transparent-only | 24,439,755 | 24,437,885 | 0 | 0 | 0 | 0 | 24,439,755 | 24,437,885 | 1,870 | same |
| 600000/3 | `b75bfcb69fa70f226467691312bbc22a8b43cfb1597310345045b2e2fa76238b` | 4 | transparent-only | 10,399,643,000 | 10,399,636,000 | 0 | 0 | 0 | 0 | 10,399,643,000 | 10,399,636,000 | 7,000 | same |
| 600000/4 | `6dd71b588cf4e5fc74d49be6da7e7f1bd739b884904e47b0aaba6f2b7fc44c79` | 4 | transparent-only | 22,216,324 | 22,214,054 | 0 | 0 | 0 | 0 | 22,216,324 | 22,214,054 | 2,270 | same |
| 600000/5 | `00c8e2ede256065b03a37936f97e85fd67206273cb3d9464d2cfc5b45911af10` | 4 | cross-pool, fully-shielded-fee, sprout | 0 | 0 | 92,000,000 | -91,990,000 | 0 | 0 | 92,000,000 | 91,990,000 | 10,000 | same |
| 600000/6 | `11853989084c48fb3580b801e7a97a7ee3be5bd2972ddc8b6f98e70bba772d05` | 4 | cross-pool, fully-shielded-fee, sprout | 0 | 0 | 9,100,000,000 | -9,099,990,000 | 0 | 0 | 9,100,000,000 | 9,099,990,000 | 10,000 | same |
| 600000/7 | `5b731fab5063e40b59672555ae8b41e840ebf0bc488dfc1ffbeab177e7d9bf9d` | 4 | cross-pool, fully-shielded-fee, sprout | 0 | 0 | 300,000,000 | -299,990,000 | 0 | 0 | 300,000,000 | 299,990,000 | 10,000 | same |
| 600000/8 | `0f27e643916bb8c26da7bab6574ca051c5d982b4309d4e61e5a095b1d00b50ed` | 4 | cross-pool, fully-shielded-fee, sprout | 0 | 0 | 8,200,000,000 | -8,199,990,000 | 0 | 0 | 8,200,000,000 | 8,199,990,000 | 10,000 | same |
| 600000/9 | `682504aedd3f60bc8c45b75d08aab4475afeca4808ab023287f6f92e52578c51` | 4 | cross-pool, fully-shielded-fee, sprout | 0 | 0 | 670,000,000 | -669,990,000 | 0 | 0 | 670,000,000 | 669,990,000 | 10,000 | same |
| 600000/10 | `8e0f8ccfcd7a205a7fff2abbe24cd0d5e9ad03cf69f240bae107fd84668e60d9` | 4 | cross-pool, fully-shielded-fee, sprout | 0 | 0 | 81,000,000 | -80,990,000 | 0 | 0 | 81,000,000 | 80,990,000 | 10,000 | same |
| 1687119/0 | `d2f915983054efb9b8af88671f323fa444e0b7af4974dd3cc92105c6b94ee42b` | 5 | coinbase, shielded-coinbase | 0 | 62,500,000 | 0 | -250,000,000 | 0 | 0 | 0 | 312,500,000 | 0 | same |
| 1687143/0 | `d9812db5d4d9c3981dad7550e9eb9e7f67930ebba1f85939ccf5406b739fe410` | 4 | coinbase | 0 | 312,501,000 | 0 | 0 | 0 | 0 | 0 | 312,501,000 | 0 | same |
| 1687143/1 | `8454f559f0c9475ae3c6746dd17e467709fba3cec84ed3dc16b911355a47b5ae` | 5 | unshielding:orchard | 0 | 10,000,000 | 0 | 0 | 10,001,000 | 0 | 10,001,000 | 10,000,000 | 1,000 | differs |
| 1687194/0 | `c10e15bd0a8b53bf5c519206865f6decdf73bc690128386f893e0c9007b7e3a9` | 5 | coinbase | 0 | 312,515,583 | 0 | 0 | 0 | 0 | 0 | 312,515,583 | 0 | same |
| 1687194/1 | `cc97ca2ca3b3e1fb4fba349cde41e867014b2342bfea0230289ac47a189d9a81` | 5 | transparent-only | 201,126,212 | 201,113,866 | 0 | 0 | 0 | 0 | 201,126,212 | 201,113,866 | 12,346 | same |
| 1687194/2 | `5e5007d455c1cb1699320285462cbf13bc0897e9b6eb91fca3191fb40c572292` | 5 | transparent-only | 956,182,510 | 956,180,501 | 0 | 0 | 0 | 0 | 956,182,510 | 956,180,501 | 2,009 | same |
| 1687194/3 | `6a94ebb500a23c3057f7c58e3f5e8f4991968e1bccff12d849a76d0015e23b21` | 5 | transparent-only | 11,491,828 | 11,491,600 | 0 | 0 | 0 | 0 | 11,491,828 | 11,491,600 | 228 | same |
| 1687194/4 | `bd84ece1a1f9930085768a880d99445ee1ee13686c2e5979b30d920e90866517` | 5 | cross-pool, fully-shielded-fee, sapling+orchard | 0 | 0 | 0 | 89,000 | -88,000 | 0 | 89,000 | 88,000 | 1,000 | differs |
| 2501409/0 | `641b74eb2c3a65189a0781cd6917d03c181ed78511127366665d5ccf1d8a3580` | 4 | coinbase | 0 | 312,512,000 | 0 | 0 | 0 | 0 | 0 | 312,512,000 | 0 | same |
| 2501409/1 | `224daf9f01485fceacfe1fa49daf4d498c23a5c58ff237d28aa1495cd8bc7c5a` | 5 | fully-shielded-fee, fully-shielded-fee:orchard | 0 | 0 | 0 | 0 | 1,000 | 0 | 1,000 | 0 | 1,000 | differs |
| 2501409/2 | `daec99857c7566ec1eb445f521a2869d6d436eac07d05453808c5745fd1ad451` | 4 | transparent-only | 711,246 | 701,246 | 0 | 0 | 0 | 0 | 711,246 | 701,246 | 10,000 | same |
| 2501409/3 | `7bd7717f8897e323840ac0f6b09518e580dbca1fa77dbd20ed0b90fc56d2fe69` | 5 | shielding:orchard | 363,000,000 | 0 | 0 | 0 | -362,999,000 | 0 | 363,000,000 | 362,999,000 | 1,000 | differs |
| 3438010/0 | `852323c5af3b5e735c72012ff61e89200b43273f56465b0bd76366fec81c0422` | 6 | coinbase | 0 | 137,570,000 | 0 | 0 | 0 | 0 | 0 | 137,570,000 | 0 | same |
| 3438010/1 | `af00b519c98cda27cca3832f09e304057bb7ba8e2f7ddda0e93778d61b60c69b` | 5 | unshielding:sapling | 0 | 10,995,028,541 | 0 | 10,995,068,541 | 0 | 0 | 10,995,068,541 | 10,995,028,541 | 40,000 | same |
| 3438010/2 | `80388d29ef0582a9c7d15b1096a564a44ec055e66cdb3edd9f557cc2888b6c55` | 6 | fully-shielded-fee, fully-shielded-fee:sapling | 0 | 0 | 0 | 10,000 | 0 | 0 | 10,000 | 0 | 10,000 | same |
| 3438010/3 | `13ceb5a350d02ba6b9b8508e748187c454b813bef49a565a425b2951ed5f7691` | 6 | ironwood, unshielding:ironwood | 0 | 10,389,748 | 0 | 0 | 0 | 10,409,748 | 10,409,748 | 10,389,748 | 20,000 | differs |
| 3442130/0 | `cbd5f4d923165f470f22c39fef63aa8d1c16a12380ae60dafafd1d94fa33de2b` | 6 | coinbase | 0 | 137,515,000 | 0 | 0 | 0 | 0 | 0 | 137,515,000 | 0 | same |
| 3442130/1 | `c30b8b7e014599442ff851f7c6bda59ab88b6a79048f4754f365bb50ab7999dc` | 6 | shielding:sapling | 3,000,000,000 | 178,985,000 | 0 | -2,821,000,000 | 0 | 0 | 3,000,000,000 | 2,999,985,000 | 15,000 | same |
| 3442400/0 | `611bb55ec14901bf1f322a18c608d883e1b140b98958f6d13cc71129cefffbde` | 6 | coinbase | 0 | 137,555,000 | 0 | 0 | 0 | 0 | 0 | 137,555,000 | 0 | same |
| 3442400/1 | `7ab09cdf4bc58098dc37d8dce2085af7010e1e4e00290246862459a3e4832fc5` | 4 | cross-pool, fully-shielded-fee, sprout | 0 | 0 | 400,000,000 | -399,960,000 | 0 | 0 | 400,000,000 | 399,960,000 | 40,000 | same |
| 3442400/2 | `b1a00ede11a2dabd31d9465d969454f152980bc85aa92bdbf5425640a39cb857` | 6 | ironwood, shielding:ironwood | 4,260,000 | 4,109,972 | 0 | 0 | 0 | -135,028 | 4,260,000 | 4,245,000 | 15,000 | differs |
| 3463373/0 | `e3f9e09fb9f8269e5bb992ffc64e33bb22b16479de3bf4727447576ed6b5285e` | 5 | coinbase | 0 | 137,746,000 | 0 | 0 | 0 | 0 | 0 | 137,746,000 | 0 | same |
| 3463373/1 | `8913f009fe5320da3b8ec82bcf8aea86c0ff42654a745c867ae01486db09bed7` | 4 | transparent-only | 845,049 | 825,049 | 0 | 0 | 0 | 0 | 845,049 | 825,049 | 20,000 | same |
| 3463373/2 | `6f37465ac2a718135c61fb62049e6205977da2042774002d301d018a76da04c8` | 6 | cross-pool, fully-shielded-fee, ironwood, sapling+orchard, three-pool | 0 | 0 | 0 | -500,000 | 24,898 | 505,102 | 530,000 | 500,000 | 30,000 | differs |
| 3463373/3 | `7e39f93eb6542dad5cdcbffaa2fb7a88524da7df3195849a44fb467cc47c1c90` | 4 | shielding:sapling | 1,878,009,839 | 0 | 0 | -1,877,909,839 | 0 | 0 | 1,878,009,839 | 1,877,909,839 | 100,000 | same |
| 3463373/4 | `5a08a1df6226ec69a3a410258316a288785f8fb9da81089421063bc36c20d741` | 4 | transparent-only | 20,527,612 | 20,511,612 | 0 | 0 | 0 | 0 | 20,527,612 | 20,511,612 | 16,000 | same |
| 3463373/5 | `afc6d16baddb4853c61d5d8d94764cf83dc0b011a0a5181a005965c63a2351f3` | 4 | transparent-only | 274,996,487 | 274,976,487 | 0 | 0 | 0 | 0 | 274,996,487 | 274,976,487 | 20,000 | same |
| 3463373/6 | `83528670357dab26b94211f39d5dcf2e7efa776d7ca68c8762e49676e0562780` | 6 | ironwood, shielding:ironwood | 125,000,000 | 0 | 0 | 0 | 0 | -124,985,000 | 125,000,000 | 124,985,000 | 15,000 | differs |
| 3463373/7 | `8691e06cef8a8d59dd176350259f405a5e88e3f884c131f4be91857d41e4d495` | 4 | transparent-only | 293,751,195 | 293,731,195 | 0 | 0 | 0 | 0 | 293,751,195 | 293,731,195 | 20,000 | same |
| 3463373/8 | `068bde0e9816e55004ed55e16dd3869fd8b247244173a3710d30e373c1bf30c6` | 4 | transparent-only | 31,887,966 | 31,877,966 | 0 | 0 | 0 | 0 | 31,887,966 | 31,877,966 | 10,000 | same |
| 3463373/9 | `c5b7ed56b5340a88645a97a683dae5b51c62b2405582fe1e4743da85e9a274b7` | 6 | ironwood, unshielding:ironwood | 0 | 10,500,000 | 0 | 0 | 0 | 10,515,000 | 10,515,000 | 10,500,000 | 15,000 | differs |
| 3479000/0 | `369e597e0573082b08171e7f08bbf96fe10dd81d017e0344e676b5d5c3e670c0` | 5 | coinbase | 0 | 137,590,000 | 0 | 0 | 0 | 0 | 0 | 137,590,000 | 0 | same |
| 3479000/1 | `af9eae20963e5e53e2bda3275ffcd1d19decad6530ce46f55b83585336819b82` | 6 | fully-shielded-fee, fully-shielded-fee:ironwood, ironwood | 0 | 0 | 0 | 0 | 0 | 40,000 | 40,000 | 0 | 40,000 | differs |
| 3479000/2 | `aee031d4299acacdfc4d1b0bfc6d34d760f5b759e187a202a917f3b297af60a2` | 6 | ironwood, unshielding:ironwood | 0 | 300,000,000 | 0 | 0 | 0 | 300,015,000 | 300,015,000 | 300,000,000 | 15,000 | differs |
| 3479000/3 | `3115796d7ebd1f35d270229221166f2318888587153a294dfb712ef620899e53` | 6 | cross-pool, fully-shielded-fee, ironwood | 0 | 0 | 0 | 0 | 1,020,000 | -1,000,000 | 1,020,000 | 1,000,000 | 20,000 | differs |
| 3479000/4 | `e6b8618cfeccce30cec8ad811768231f82bb1f93b8cec4d789537cdf2bde8f1b` | 6 | ironwood, shielding:ironwood | 661,586,470 | 659,438,801 | 0 | 0 | 0 | -2,132,669 | 661,586,470 | 661,571,470 | 15,000 | differs |

## Descriptions

- `600000/0` Coinbase with 2 transparent outputs, 1,250,073,780 zatoshi.
- `600000/1` Transparent only: 1 input, 2 outputs, fee 2,640 zatoshi.
- `600000/2` Transparent only: 2 inputs, 2 outputs, fee 1,870 zatoshi.
- `600000/3` Transparent only: 1 input, 2 outputs, fee 7,000 zatoshi.
- `600000/4` Transparent only: 1 input, 2 outputs, fee 2,270 zatoshi.
- `600000/5` Sprout to Sapling, no transparent side: 92,000,000 zatoshi leave Sprout; 91,990,000 zatoshi enter Sapling; fee 10,000 zatoshi.
- `600000/6` Sprout to Sapling, no transparent side: 9,100,000,000 zatoshi leave Sprout; 9,099,990,000 zatoshi enter Sapling; fee 10,000 zatoshi.
- `600000/7` Sprout to Sapling, no transparent side: 300,000,000 zatoshi leave Sprout; 299,990,000 zatoshi enter Sapling; fee 10,000 zatoshi.
- `600000/8` Sprout to Sapling, no transparent side: 8,200,000,000 zatoshi leave Sprout; 8,199,990,000 zatoshi enter Sapling; fee 10,000 zatoshi.
- `600000/9` Sprout to Sapling, no transparent side: 670,000,000 zatoshi leave Sprout; 669,990,000 zatoshi enter Sapling; fee 10,000 zatoshi.
- `600000/10` Sprout to Sapling, no transparent side: 81,000,000 zatoshi leave Sprout; 80,990,000 zatoshi enter Sapling; fee 10,000 zatoshi.
- `1687119/0` Coinbase that pays 250,000,000 zatoshi into Sapling and 62,500,000 zatoshi to 3 transparent outputs.
- `1687143/0` Coinbase with 4 transparent outputs, 312,501,000 zatoshi.
- `1687143/1` Unshielding from Orchard: 10,001,000 zatoshi leave Orchard; 1 transparent output (10,000,000 zatoshi); fee 1,000 zatoshi.
- `1687194/0` Coinbase with 4 transparent outputs, 312,515,583 zatoshi.
- `1687194/1` Transparent only: 1 input, 1 output, fee 12,346 zatoshi.
- `1687194/2` Transparent only: 1 input, 54 outputs, fee 2,009 zatoshi.
- `1687194/3` Transparent only: 15 inputs, 1 output, fee 228 zatoshi.
- `1687194/4` Sapling to Orchard, no transparent side: 89,000 zatoshi leave Sapling; 88,000 zatoshi enter Orchard; fee 1,000 zatoshi.
- `2501409/0` Coinbase with 4 transparent outputs, 312,512,000 zatoshi.
- `2501409/1` Fully shielded, Orchard only: 1,000 zatoshi leave the pool and all of it is the fee. A positive balance, but not an unshielding.
- `2501409/2` Transparent only: 45 inputs, 2 outputs, fee 10,000 zatoshi.
- `2501409/3` Shielding into Orchard: 1 transparent input (363,000,000 zatoshi); 362,999,000 zatoshi enter Orchard; fee 1,000 zatoshi.
- `3438010/0` Coinbase with 2 transparent outputs, 137,570,000 zatoshi.
- `3438010/1` Unshielding from Sapling: 10,995,068,541 zatoshi leave Sapling; 1 transparent output (10,995,028,541 zatoshi); fee 40,000 zatoshi.
- `3438010/2` Fully shielded, Sapling only: 10,000 zatoshi leave the pool and all of it is the fee. A positive balance, but not an unshielding.
- `3438010/3` Unshielding from Ironwood: 10,409,748 zatoshi leave Ironwood; 1 transparent output (10,389,748 zatoshi); fee 20,000 zatoshi.
- `3442130/0` Coinbase with 2 transparent outputs, 137,515,000 zatoshi.
- `3442130/1` Shielding into Sapling: 2 transparent inputs (3,000,000,000 zatoshi); 2,821,000,000 zatoshi enter Sapling; 1 transparent output (178,985,000 zatoshi); fee 15,000 zatoshi.
- `3442400/0` Coinbase with 2 transparent outputs, 137,555,000 zatoshi.
- `3442400/1` Sprout to Sapling, no transparent side: 400,000,000 zatoshi leave Sprout; 399,960,000 zatoshi enter Sapling; fee 40,000 zatoshi.
- `3442400/2` Shielding into Ironwood: 1 transparent input (4,260,000 zatoshi); 135,028 zatoshi enter Ironwood; 1 transparent output (4,109,972 zatoshi); fee 15,000 zatoshi.
- `3463373/0` Coinbase with 2 transparent outputs, 137,746,000 zatoshi.
- `3463373/1` Transparent only: 1 input, 1 output, fee 20,000 zatoshi.
- `3463373/2` Three pools, no transparent side: 24,898 zatoshi leave Orchard; 505,102 zatoshi leave Ironwood; 500,000 zatoshi enter Sapling; fee 30,000 zatoshi.
- `3463373/3` Shielding into Sapling: 15 transparent inputs (1,878,009,839 zatoshi); 1,877,909,839 zatoshi enter Sapling; fee 100,000 zatoshi.
- `3463373/4` Transparent only: 2 inputs, 1 output, fee 16,000 zatoshi.
- `3463373/5` Transparent only: 1 input, 2 outputs, fee 20,000 zatoshi.
- `3463373/6` Shielding into Ironwood: 1 transparent input (125,000,000 zatoshi); 124,985,000 zatoshi enter Ironwood; fee 15,000 zatoshi.
- `3463373/7` Transparent only: 1 input, 1 output, fee 20,000 zatoshi.
- `3463373/8` Transparent only: 1 input, 2 outputs, fee 10,000 zatoshi.
- `3463373/9` Unshielding from Ironwood: 10,515,000 zatoshi leave Ironwood; 1 transparent output (10,500,000 zatoshi); fee 15,000 zatoshi.
- `3479000/0` Coinbase with 2 transparent outputs, 137,590,000 zatoshi.
- `3479000/1` Fully shielded, Ironwood only: 40,000 zatoshi leave the pool and all of it is the fee. A positive balance, but not an unshielding.
- `3479000/2` Unshielding from Ironwood: 300,015,000 zatoshi leave Ironwood; 1 transparent output (300,000,000 zatoshi); fee 15,000 zatoshi.
- `3479000/3` Orchard to Ironwood, no transparent side: 1,020,000 zatoshi leave Orchard; 1,000,000 zatoshi enter Ironwood; fee 20,000 zatoshi.
- `3479000/4` Shielding into Ironwood: 2 transparent inputs (661,586,470 zatoshi); 2,132,669 zatoshi enter Ironwood; 1 transparent output (659,438,801 zatoshi); fee 15,000 zatoshi.

## Not in the dataset

- Value entering the Sprout pool (`vpub_old` greater than zero). It has not been possible on mainnet since the
  Canopy upgrade in 2020, which is before the range the correction affects.
- A coinbase transaction that pays into Orchard or Ironwood.
- A transaction for which GraphSense's coinjoin flag is set.
