// Create a collection
db.createCollection("collection_name");

// Drop a collection
db.collection_name.drop();

// Insert a document
db.collection_name.insertOne({name: "John", age: 30});

// Insert multiple documents
db.collection_name.insertMany([
    {name: "John", age: 30},
    {name: "Jane", age: 25}
]);

// Check all documents in a collection
db.collection_name.find();

// Check a specific document
db.collection_name.findOne({name: "John"});

// Check sub documents of a document
db.collection_name.findOne({name: "John"}).subdocuments;

// Check the number of documents in a collection
db.collection_name.countDocuments();

// Update a document
db.collection_name.updateOne({name: "John"}, {$set: {age: 31}});

// Delete a document
db.collection_name.deleteOne({name: "John"});

// Delete all documents in a collection
db.collection_name.deleteMany({});



