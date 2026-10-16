"""The two figures of GraphSense's Spark transformation that compare-range needs and that exist only in Scala.

Both follow graphsense-lib's `spark/src/main/scala/org/graphsense/utxo/` (identical from spark-v26.08.2 to
spark-v26.09.1) line by line, on the stored rows the worker returns.

Relation estimate (`Transformator.plainAddressRelations`, lines 199-254, fed by `TransformationJob` lines 137-176):
    per transaction, every single-address input and output is netted per address (`computeAddressTransactions`,
    Transformation.scala 310-332); addresses with a negative net are the senders, those with a positive net the
    receivers (`splitTransactions`, 224-233). With
        totalInput = sum of the values of all inputs, shielded ones included   (coinbase: the total output)
        regularSum = sum of the single-address inputs
        addressSum = sum of the senders' net values
    each sender-receiver pair gets
        round(inValue / (totalInput - regularSum + addressSum) * outValue),  or 0 if the denominator is 0.
    Spark divides the two longs as doubles and `round` rounds the double half up through
    `java.math.BigDecimal.valueOf` (Spark 3.5.8, mathExpressions.scala, RoundBase); `half_up` does the same.
    graphsense-lib's delta updater computes the same formula in Python (`dbdelta_from_utxo_transaction`), but with
    Python's `round`, which rounds half to even.

Clustering input set (`Transformator.plainAddressCluster`, lines 73-96): the distinct single-address inputs of a
transaction, if there are at least two, leaving out coinjoin transactions when coinjoin filtering is on.
"""

from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

COINBASE = "coinbase"  # the address Spark gives a coinbase transaction's single input (Transformation.scala 111-126)


def half_up(value):
    """Round a double as Spark's `round` does: half up, on the decimal form `BigDecimal.valueOf` reads."""
    return int(Decimal(repr(value)).to_integral_value(rounding=ROUND_HALF_UP))


def estimates(record):
    """{(sender, receiver): estimated value} for one transaction record."""
    if record["coinbase"]:
        inputs = [(COINBASE, record["total_output"])]
        total_input = record["total_output"]
    else:
        inputs = record["regular_inputs"]
        total_input = record["total_input"]
    regular_sum = sum(value for _, value in inputs)
    net: defaultdict[str, int] = defaultdict(int)
    for address, value in inputs:
        net[address] -= value
    for address, value in record["regular_outputs"]:
        net[address] += value
    senders = {address: -value for address, value in net.items() if value < 0}
    receivers = {address: value for address, value in net.items() if value > 0}
    if not senders or not receivers:
        return {}
    denominator = total_input - regular_sum + sum(senders.values())
    return {
        (sender, receiver): 0 if denominator == 0 else half_up(in_value / denominator * out_value)
        for sender, in_value in senders.items()
        for receiver, out_value in receivers.items()
    }


def cluster_set(record, coinjoin_filtering):
    """Spark's clustering input set of one transaction (sorted), or None when it contributes no edge."""
    if record["coinbase"] or (coinjoin_filtering and record["coinjoin"] is not False):
        return None
    addresses = sorted({address for address, _ in record["regular_inputs"]})
    return addresses if len(addresses) > 1 else None
