// ====================================================================
// Measurements about stop points
//
// Every measurement is a named object, never a bare slot on the station.
// passagierfrequenz holds one figure per station *per year*: writing
// `station[dailyTraffic -> 37900]` would put 2018 and 2024 into two
// competing values of one slot, with nothing to tell them apart.
// Naming the observation keeps the period and the source attached.
// ====================================================================

Observation {
    aboutStopPoint: StopPoint,
    period: String,
    value: Float
}.

declare required Observation aboutStopPoint.

PassengerFrequency extends Observation.
StationUsage extends Observation.

// --------------------------------------------------------------------
// Derived comparisons
//
// FrameX compares values that are already present; it does not compute
// them. Thresholds are stated openly here rather than buried in Python,
// so "busy" is an inspectable claim rather than a magic number.
// --------------------------------------------------------------------
?S[observedFrequency(?Y) -> ?V] <-
    ?O:PassengerFrequency AND ?O[aboutStopPoint -> ?S]
    AND ?O[period -> ?Y] AND ?O[value -> ?V].

// Historical existential classification: any recorded year may qualify.
// Use busyIn(year) for a specific reporting period.
?S:BusyStation <-
    ?O:PassengerFrequency AND ?O[aboutStopPoint -> ?S]
    AND ?O[value -> ?V] AND ?V > 20000.

?S:MajorHub <- ?S:BusyStation AND ?S:Junction AND ?S:LongDistanceStation.

?S[busyIn(?Y) -> true] <- ?S[observedFrequency(?Y) -> ?V] AND ?V > 20000.
// The selected period qualifies passenger frequency; infrastructure and
// service still describe their separately dated loaded snapshots.
?S[majorHubIn(?Y) -> true] <- ?S[busyIn(?Y) -> true]
    AND ?S:Junction AND ?S:LongDistanceStation.
