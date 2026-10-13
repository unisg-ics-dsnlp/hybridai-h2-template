// ====================================================================
// Infrastructure: lines and their operating points
//
// "There is a railway line between A and B" is a different claim from
// "a train actually ran from A to B". This layer holds the first;
// 40-service.fx holds the second. Keeping them apart is what lets an
// answer say which kind of evidence it rests on.
// ====================================================================

Line { label: String, lineName: String }.

// The station-on-line relation is reified: it carries a kilometre mark,
// and a bare edge cannot hold an attribute. This is the "named
// relationship object" pattern -- roles made explicit so evidence can
// attach to them.
LinePoint {
    atStopPoint: StopPoint,
    onLine: Line,
    kilometre: Float,
    pointAbbreviation: String
}.

declare required LinePoint atStopPoint.
declare required LinePoint onLine.

// --------------------------------------------------------------------
// Derived line membership
// --------------------------------------------------------------------
?S[servedByLine -> ?L] <- ?P:LinePoint AND ?P[atStopPoint -> ?S] AND ?P[onLine -> ?L].

declare inverse servedByLine servesStopPoint.

// "Two stations on a common line" is deliberately NOT a rule.
//
// Materialising it costs all pairs of stations per line, summed over
// every line, and `declare symmetric` doubles that. Measured on the real
// network it took one query from 7s to 65s on its own -- the same
// mistake as an unbounded transitive closure, and for the same reason.
//
// Ask it as a join instead, which costs only the pairs you asked about:
//
//   ?- ?A[servedByLine -> ?L] AND ?B[servedByLine -> ?L] AND ?A != ?B.
//
// Or, for one known station:
//
//   ?- sp_8509000[servedByLine -> ?L] AND ?B[servedByLine -> ?L]
//      AND ?B != sp_8509000.
//
// Sharing a line is in any case not the same as being reachable: a line
// has a direction and a service pattern. 40-service.fx holds that.

// A junction: a stop point where more than one line meets. Cheap, because
// it is bounded per station rather than over pairs of stations.
?S:Junction <-
    ?S[servedByLine -> ?L1] AND ?S[servedByLine -> ?L2] AND ?L1 != ?L2.
