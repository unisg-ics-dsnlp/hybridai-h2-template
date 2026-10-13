// ====================================================================
// Geography and transport modes
//
// Cantons, municipalities and modes are objects, not strings, so
// "every station in Graubünden" is a join instead of a text match and
// the answer can be explained.
// ====================================================================

Place { label: String }.
Canton extends Place.
District extends Place.
Municipality extends Place.
Locality extends Place.

TransportMode { label: String }.

// A stop point sits in exactly one canton and municipality, but may
// serve several modes: the source column is pipe-delimited ("BUS|TRAIN"),
// which the mapper splits into one fact per mode.
declare functional inCanton.
declare functional inMunicipality.

// --------------------------------------------------------------------
// Mode-derived classes
//
// Only rail is in scope for this knowledge base, but the other modes are
// kept as data so the scope can be widened later without re-ingesting.
// --------------------------------------------------------------------
?S:TramStopPoint <- ?S:PassengerStopPoint AND ?S[servesMode -> mode_tram].
?S:BusStopPoint <- ?S:PassengerStopPoint AND ?S[servesMode -> mode_bus].

// A station where a rail passenger can change to another mode. Useful in
// its own right, and the reason modes are modelled as objects.
?S:Interchange <- ?S:TrainStopPoint AND ?S[servesMode -> ?M] AND ?M != mode_train.
