# CN-DSI

This repository contains only one source code file for the WhatsApp-to-AWS transcription workflow: [lambda_function.py](lambda_function.py), the code that the Lambda function runs. However, most of the set-up is outside of Lambda. This README describes how the whole AWS account was configured.

The diagram below is an overview of the whole workflow. Each of the steps, and how to set them up, will be described with screenshots of the AWS web GUI.

![](README-img/aws-for-causa-natura.svg)

## WhatsApp Business Account

The first step is to get a WhatsApp Business Account, such as this:

![](README-img/whatsapp-business-account.png)

It's a phone number that WhatsApp users can send messages to, and it can join a WhatsApp group to get all messages sent to that group.

## AWS Social Messaging

Next, the WhatsApp Business Account should be added to AWS End User Messaging > Social messaging > WhatsApp: Business accounts, like this:

![](README-img/aws-social-messaging.png)

(Note that this is a different account from the one shown in the previous section.)

## SNS (Simple Notification Service)

Messages are sent from Social Messaging to Lambda through an SNS queue: Social Messaging publishes to an SNS topic and Lambda subscribes to it. Below is an SNS topic:

![](README-img/sns-topic.png)

Publications are set up in the Social Messaging GUI, so go back to this GUI and click on the "Event destination" tab.

![](README-img/set-up-publication.png)

An SNS topic can have more than one subscriber, and you might want to set up an email subscription for testing. To do that, click on "Create subscription," choose the "Email" protocol, and enter the email address as an endpoint.

![](README-img/set-up-email-subscription.png)

And then respond to the email with "Confirm subscription." It may be in your spam folder.

![](README-img/set-up-email-subscription-2.png)

Now whenever the WhatsApp Business Account receives a message, you'll get an email containing the JSON structure that will be passed to Lambda.

## VPC (Virtual Private Cloud)

The next step would be to set up a Lambda function that subscribes to the SNS topic, but this Lambda function needs to be inside of a VPC for complicated reasons:
* the function needs large files (Python libraries and a speech recognition model) to run,
* these files won't fit in a ZIP archive, so we'll need to put them in an EFS filesystem and attach that filesystem to the Lambda,
* an EFS filesystem must be on a private subnet in a VPC.
And furthermore, the VPC must be set up with a public-private bridge, so that it can access network resources outside the VPC, because
* AWS Social Messaging is outside of the VPC,
* the Lambda function will need to contact Social Messaging to tell it to send audio messages to S3.

Thus, this complicated set-up is required because we want both (1) large dependencies and (2) to access audio messages.

In the VPC GUI, under "Your VPCs," there is already a default VPC set up with 6 subnets for each of the us-east-1 Access Zones. _This is not the VPC we want!_

![](README-img/default-vpc.png)

Instead, we want to make a new VPC with only 2 subnets, one public and one private. Under "Your VPCs," click on the orange "Create VPC" button. Give this VPC a name so that it's easy to distinguish from the default, and set its "IPv4 CIDR" to

```
10.0.0.0/16
```

a large range of internal addresses that we will subdivide into the two subnets.

![](README-img/create-vpc.png)

Under "Subnets," create the first subnet with the orange "Create subnet" button, assign it to the new VPC, name it "private," set its Availability Zone (I chose `us-east-1a`), and set its "IPv4 subnet CIDR block" to

```
10.0.1.0/24
```

![](README-img/create-vpc-private.png)

Do the same thing to make a "public" subnet in the same Availability Zone, but set its "IPv4 subnet CIDR block" to

```
10.0.2.0/24
```

![](README-img/create-vpc-public.png)

The subnet list shows all subnets for all VPCs, so your two new ones are mixed in with the 6 subnets for the default VPC. This is why it's important to give them names.

There isn't anything "private" or "public" about these subnets until we set up their route tables, which send traffic on these subnets through an Internet Gateway or a NAT Gateway. The following steps must be performed in the order described, since each object must be accessible at the time of its creation.

First, create an Internet Gateway. The "Internet gateways" list item is in the left sidebar and the orange "Create internet gateway" is in the top-right. Since the original VPC also has an Internet Gateway, you'll see that in the list, but we're making a new one to connect it to the new VPC. The creation form only lets you set its name:

![](README-img/internet-gateway.png)

but you're not done until you attach it to the new VPC.

![](README-img/internet-gateway-2.png)

![](README-img/internet-gateway-3.png)

Its "State" should now be "Attached" (and green).

Next, use the "Route tables" left sidebar item to see the Route Tables. Before creating any, there's already a Route Table for each VPC, which merely connects internal traffic within each VPC. We will create two new Route Tables, one for each Subnet, and then wire them up. The orange "Create route table" button is in the upper-right. Create two blank Route Tables with names indicating private and public in the new VPC.

![](README-img/route-tables-1.png)

Next, select each one and use the "Subnet associations" tab to associate the private one with the private Subnet and the public one with the public Subnet.

![](README-img/route-tables-2.png)

![](README-img/route-tables-3.png)

Now they should look like this:

![](README-img/route-tables-4.png)

Next, in the public Route Table, click on the "Routes" tab and the "Edit routes" button:

![](README-img/route-tables-5.png)

Then use the "Add route" button to add a new row. Set its "Destination" to `0.0.0.0/0` (all IP addresses) and its "Target" to the new Internet Gateway.

![](README-img/route-tables-6.png)

Before we can set up the private Route Table, we have to create a NAT Gateway, and before we can create a NAT Gateway, we need to allocate an Elastic IP. So find the "Elastic IPs" list item in the left sidebar and the orange "Allocate Elastic IP address" in the top-right. There aren't any required configuration parameters, but let's give it a name by setting the `Name` Tag.

![](README-img/elastic-ip.png)

Keep in mind that while your Elastic IP is allocated but unassociated, it will cost about $4 per month. To associate it, we need to create the NAT Gateway. There's a "NAT gateways" list item in the left sidebar; click on the orange "Create NAT gateway" in the upper-right to get the following form. Put the NAT Gateway in the public Subnet, not private.

![](README-img/nat-gateway.png)

Bafck to the "Route tables" list item in the left sidebar, select the private Route Table, its "Routes" tab, and "Edit routes". Then use "Add route" to make a new row, set its "Destination" to `0.0.0.0/0` and its "Target" to the new NAT Gateway.

![](README-img/route-tables-7.png)

Now we have a VPC in which to put the EFS filesystem and the Lambda function.

## EFS filesystem
