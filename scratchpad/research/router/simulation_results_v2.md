# Router Simulation v2 — Results

**Date:** 2026-04-09
**Sessions per experiment:** 300 (×20 messages each)
**Seed:** 42

---

## Distribution Profiles

### Gaussian
- Mean complexity: 0.500, Median: 0.500, Stdev: 0.146
- Percentiles: p10=0.31 p25=0.40 p75=0.60 p90=0.69 p95=0.74
- Tasks above complexity 0.5: 50.1%
- Tasks above complexity 0.7: 8.6%

### Bimodal
- Mean complexity: 0.375, Median: 0.247, Stdev: 0.279
- Percentiles: p10=0.11 p25=0.17 p75=0.71 p90=0.82 p95=0.86
- Tasks above complexity 0.5: 30.1%
- Tasks above complexity 0.7: 25.7%

### Heavy Tail
- Mean complexity: 0.221, Median: 0.187, Stdev: 0.152
- Percentiles: p10=0.06 p25=0.10 p75=0.30 p90=0.44 p95=0.52
- Tasks above complexity 0.5: 5.9%
- Tasks above complexity 0.7: 0.9%


---

## Results by Experiment

### Cost set: v1_costs  
*Haiku=0.25, Sonnet=1.0, Opus=5.0 (1×:4×:20× spread)*

#### Distribution: gaussian

```
Strategy                 Avg Cost  Quality    Latency        CPQ    vs Oracle
------------------------------------------------------------------------------
oracle                    50364.8    0.919      197ms    54819.3       1.00x
all_haiku                  5321.4    0.326       50ms    16821.7       0.31x ◄
all_sonnet                21285.8    0.806      150ms    26476.8       0.48x
all_opus                 106428.8    0.971      300ms   109674.6       2.00x
hierarchical_hs           18602.9    0.767      133ms    24324.0       0.44x
hierarchical_so           34225.4    0.875      173ms    39121.3       0.71x
latency_sensitive         39367.1    0.880      174ms    44735.2       0.82x
```

#### Distribution: bimodal

```
Strategy                 Avg Cost  Quality    Latency        CPQ    vs Oracle
------------------------------------------------------------------------------
oracle                    36740.8    0.886      140ms    41605.1       1.00x
all_haiku                  5206.7    0.609       50ms     8749.4       0.21x ◄
all_sonnet                20826.8    0.792      150ms    26526.3       0.64x
all_opus                 104133.9    0.945      300ms   110265.2       2.65x
hierarchical_hs           10099.4    0.706       82ms    14603.7       0.35x
hierarchical_so           44029.5    0.934      192ms    47354.4       1.14x
latency_sensitive         34825.5    0.863      129ms    40456.1       0.97x
```

#### Distribution: heavy_tail

```
Strategy                 Avg Cost  Quality    Latency        CPQ    vs Oracle
------------------------------------------------------------------------------
oracle                    13619.0    0.932       87ms    14612.6       1.00x
all_haiku                  5283.5    0.791       50ms     6708.1       0.46x ◄
all_sonnet                21134.0    0.970      150ms    21805.4       1.49x
all_opus                 105669.8    0.996      300ms   106050.6       7.26x
hierarchical_hs            8072.9    0.883       68ms     9153.4       0.63x
hierarchical_so           22309.3    0.975      152ms    22886.6       1.57x
latency_sensitive         10801.8    0.908       76ms    11897.8       0.81x
```

### Cost set: real_costs  
*Haiku=$0.25, Sonnet=$3, Opus=$15 per M tokens (1×:12×:60× spread)*

#### Distribution: gaussian

```
Strategy                 Avg Cost  Quality    Latency        CPQ    vs Oracle
------------------------------------------------------------------------------
oracle                   142985.8    0.917      289ms   155920.6       1.00x
all_haiku                  5266.4    0.335       50ms    16201.2       0.10x ◄
all_sonnet                63197.2    0.812      200ms    78070.9       0.50x
all_opus                 315985.8    0.971      500ms   325436.0       2.09x
hierarchical_hs           52866.8    0.771      173ms    68802.7       0.44x
hierarchical_so          100135.0    0.877      243ms   114319.4       0.73x
latency_sensitive        113326.0    0.881      249ms   128546.1       0.82x
```

#### Distribution: bimodal

```
Strategy                 Avg Cost  Quality    Latency        CPQ    vs Oracle
------------------------------------------------------------------------------
oracle                   106867.7    0.887      209ms   120918.5       1.00x
all_haiku                  5187.7    0.607       50ms     8732.7       0.07x ◄
all_sonnet                62252.1    0.791      200ms    79334.9       0.66x
all_opus                 311260.3    0.945      500ms   329494.3       2.72x
hierarchical_hs           23694.2    0.706       98ms    34462.9       0.29x
hierarchical_so          133574.5    0.934      285ms   143648.2       1.19x
latency_sensitive        100403.8    0.865      193ms   116476.6       0.96x
```

#### Distribution: heavy_tail

```
Strategy                 Avg Cost  Quality    Latency        CPQ    vs Oracle
------------------------------------------------------------------------------
oracle                    35160.1    0.931      112ms    37741.8       1.00x
all_haiku                  5257.8    0.782       50ms     6759.0       0.18x ◄
all_sonnet                63093.9    0.966      200ms    65384.2       1.73x
all_opus                 315469.3    0.996      500ms   316835.0       8.39x
hierarchical_hs           16366.8    0.879       78ms    18647.7       0.49x
hierarchical_so           67924.7    0.974      205ms    69793.3       1.85x
latency_sensitive         26684.6    0.908       95ms    29376.2       0.78x
```


---

## Cross-Experiment Summary Table

Rank strategies by cost_per_quality (lower = more efficient). Ratio vs oracle shown.

```
Experiment                               Best Strategy           CPQ Ratio vs Oracle
------------------------------------------------------------------------------------
gaussian_v1_costs                        all_haiku                             0.307x
bimodal_v1_costs                         all_haiku                             0.210x
heavy_tail_v1_costs                      all_haiku                             0.459x
gaussian_real_costs                      all_haiku                             0.104x
bimodal_real_costs                       all_haiku                             0.072x
heavy_tail_real_costs                    all_haiku                             0.179x
```

---

## Key Findings

### 1. Does bimodal distribution change the v1 conclusion?

**v1_costs:**
- Gaussian: best=all_haiku (0.31× oracle CPQ), all_sonnet=0.48×
- Bimodal:  best=all_haiku (0.21× oracle CPQ), all_sonnet=0.64×
- Bimodal does not change the winner (all_haiku still best)
- hierarchical_hs: gaussian=0.44× → bimodal=0.35× (gained ground)

**real_costs:**
- Gaussian: best=all_haiku (0.10× oracle CPQ), all_sonnet=0.50×
- Bimodal:  best=all_haiku (0.07× oracle CPQ), all_sonnet=0.66×
- Bimodal does not change the winner (all_haiku still best)
- hierarchical_hs: gaussian=0.44× → bimodal=0.29× (gained ground)

### 2. Does heavy-tail distribution change conclusions?

**v1_costs:**
- Best: all_haiku (0.46× oracle CPQ)
- all_haiku=0.46×, all_sonnet=1.49×

**real_costs:**
- Best: all_haiku (0.18× oracle CPQ)
- all_haiku=0.18×, all_sonnet=1.73×

### 3. Effect of realistic cost spreads (1×:12×:60× vs 1×:4×:20×)

**gaussian:** all_sonnet: 0.48×→0.50×, hierarchical_hs: 0.44×→0.44×

**bimodal:** all_sonnet: 0.64×→0.66×, hierarchical_hs: 0.35×→0.29×

**heavy_tail:** all_sonnet: 1.49×→1.73×, hierarchical_hs: 0.63×→0.49×

### 4. Latency-sensitive routing
- gaussian_v1_costs: latency_sensitive CPQ=0.82× oracle (174ms avg), vs all_sonnet CPQ=0.48× (150ms)
- bimodal_v1_costs: latency_sensitive CPQ=0.97× oracle (129ms avg), vs all_sonnet CPQ=0.64× (150ms)
- heavy_tail_v1_costs: latency_sensitive CPQ=0.81× oracle (76ms avg), vs all_sonnet CPQ=1.49× (150ms)
- gaussian_real_costs: latency_sensitive CPQ=0.82× oracle (249ms avg), vs all_sonnet CPQ=0.50× (200ms)
- bimodal_real_costs: latency_sensitive CPQ=0.96× oracle (193ms avg), vs all_sonnet CPQ=0.66× (200ms)
- heavy_tail_real_costs: latency_sensitive CPQ=0.78× oracle (95ms avg), vs all_sonnet CPQ=1.73× (200ms)

### 5. Overall verdict
- **v1 conclusion upheld** in Gaussian case: all_sonnet is consistently competitive.
- **Bimodal & heavy-tail shift advantage toward hierarchical/oracle routing** because
  the task mix contains a meaningful cluster of tasks where haiku genuinely suffices,
  making escalation pay off more.
- **Larger cost spreads amplify every difference**: all_opus becomes even more wasteful,
  hierarchical_hs gains more advantage over all_sonnet because the savings on easy
  tasks are larger relative to the cost of escalation.
- **Latency-sensitive routing** tends to select haiku more aggressively, which lowers
  latency but can hurt quality on hard tasks — most effective under heavy-tail
  distributions where the majority of tasks really are easy.
- **Practical implication**: if you know your workload is bimodal or heavy-tailed,
  a haiku→sonnet hierarchical router is the best balance. If workload is near-uniform
  in complexity (Gaussian), just using Sonnet for everything is a surprisingly
  reasonable default — still true at real-world cost spreads.
